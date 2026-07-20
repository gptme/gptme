"""
V2 API for gptme server with improved control flow and conversation management.

This module contains the main conversation CRUD endpoints for the V2 API.
Session management, tool execution, and agent creation are handled by separate modules.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import asdict, replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Literal, cast

import flask
import tomlkit
from dateutil.parser import isoparse
from flask import request

from gptme.__version__ import __version__
from gptme.config import (
    ChatConfig,
    Config,
    get_config,
    load_user_config,
    set_config,
    set_config_value,
)
from gptme.credentials import get_stored_api_key
from gptme.llm import PROVIDER_API_KEYS, list_available_providers
from gptme.llm.constants import OPENROUTER_APP_HEADERS
from gptme.llm.models import (
    PROVIDERS,
    Provider,
    _apply_model_filters,
    _get_models_for_provider,
    get_default_model,
    get_model,
    get_recommended_model,
    set_default_model,
)
from gptme.llm.models.types import is_custom_provider
from gptme.prompts import get_prompt

from ..commands import handle_cmd
from ..config import get_project_config
from ..config.user import (
    get_default_model_source,
    get_user_config_env_source,
    get_user_config_paths,
    get_user_config_runtime_info,
)
from ..dirs import get_logs_dir
from ..logmanager import (
    ConversationMeta,
    Log,
    LogManager,
    get_conversation_meta_direct,
    get_user_conversations,
)
from ..logmanager import conversations as conversations_module
from ..message import Message
from ..tools import get_toolchain, get_tools, init_tools
from ..util.content import is_message_command
from ..util.uri import URI, FilePath, is_uri, parse_file_reference
from .api_v2_agents import agents_api
from .api_v2_common import (
    _abs_to_rel_workspace,
    _validate_branch,
    _validate_conversation_id,
    msg2dict,
)
from .api_v2_sessions import SessionManager, sessions_api
from .auth import require_auth
from .external_sessions import get_external_session_provider
from .metrics import record_cache_result, update_conversation_metrics
from .openapi_docs import (
    API_VERSION,
    CONTRACT_REVISION,
    CONVERSATION_ID_PARAM,
    ApiRootResponse,
    ApiVersionResponse,
    AudioTranscriptionResponse,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ErrorResponse,
    ExternalSessionListResponse,
    ExternalSessionResponse,
    ExternalSessionSteerResponse,
    MessageCreateRequest,
    SessionResponse,
    StatusResponse,
    UserApiKeySaveRequest,
    UserApiKeySaveResponse,
    UserConfigFilePatchRequest,
    UserConfigFilePatchResponse,
    UserConfigFileResponse,
    UserConfigFileSaveRequest,
    UserConfigFileSaveResponse,
    UserDefaultModelSaveRequest,
    UserDefaultModelSaveResponse,
    UserFavoritesSaveRequest,
    UserFavoritesSaveResponse,
    UserSettingsResponse,
    api_doc,
    api_doc_simple,
)

logger = logging.getLogger(__name__)

# Raster image extensions allowed for user avatars.
# SVG excluded: can embed <script> tags (XSS via crafted SVG).
_ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico"}

# Commands that must not run in server mode:
# - exit/restart: terminate or restart the server process
# - edit: launches an interactive $EDITOR subprocess, blocking the worker thread
# - delete (without --force): calls input() waiting for stdin that never arrives
_SERVER_BLOCKED_COMMANDS = {"exit", "restart", "edit", "delete"}


def _parse_conversation_cursor(raw: str) -> tuple[float, str | None]:
    """Parse a conversation pagination cursor.

    Supports both the legacy numeric timestamp cursor and the opaque composite
    cursor returned by the paginated API: ``<modified>|<quoted-conversation-id>``.
    """
    if "|" not in raw:
        return float(raw), None

    modified_raw, quoted_conv_id = raw.split("|", 1)
    if not quoted_conv_id:
        raise ValueError("missing conversation id")
    return float(modified_raw), urllib.parse.unquote(quoted_conv_id)


def _encode_conversation_cursor(conv: ConversationMeta) -> str:
    """Encode a stable pagination cursor for a conversation."""
    return f"{conv.modified:.17g}|{urllib.parse.quote(conv.id, safe='')}"


_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_DEFAULT_OPENROUTER_STT_MODEL = "openai/whisper-1"
_MAX_TRANSCRIPTION_AUDIO_BYTES = 25 * 1024 * 1024
_SUPPORTED_TRANSCRIPTION_FORMATS = {"wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY_ENV_VALUES


def _guess_audio_format(filename: str | None, mimetype: str | None) -> str | None:
    """Infer an OpenRouter STT audio format from filename or MIME type."""
    if mimetype:
        mime_key = mimetype.split(";", 1)[0].strip().lower()
        mime_map = {
            "audio/aac": "aac",
            "audio/flac": "flac",
            "audio/m4a": "m4a",
            "audio/mp3": "mp3",
            "audio/mp4": "m4a",
            "audio/mpeg": "mp3",
            "audio/ogg": "ogg",
            "audio/wav": "wav",
            "audio/webm": "webm",
            "audio/x-wav": "wav",
        }
        if mime_key in mime_map:
            return mime_map[mime_key]

    if filename:
        suffix = Path(filename).suffix.lower().lstrip(".")
        if suffix in _SUPPORTED_TRANSCRIPTION_FORMATS:
            return suffix
    return None


def _transcribe_audio_with_openrouter(
    audio_bytes: bytes, audio_format: str, model: str, language: str | None = None
) -> dict:
    """Call OpenRouter's STT endpoint and return the decoded JSON response."""
    config = get_config()
    api_key = config.get_env("OPENROUTER_API_KEY") or get_stored_api_key("openrouter")
    if not api_key:
        raise ValueError("OpenRouter is not configured on this server")

    payload: dict[str, object] = {
        "model": model,
        "input_audio": {
            "data": b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
    }
    if language:
        payload["language"] = language

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/audio/transcriptions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **OPENROUTER_APP_HEADERS,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return cast(dict, json.loads(response.read().decode("utf-8")))


def _get_webui_deploy_config() -> dict[str, object]:
    """Return web UI deploy trigger config without exposing the GitHub token."""
    repository = os.environ.get("GPTME_WEBUI_DEPLOY_REPOSITORY", "gptme/gptme").strip()
    workflow = os.environ.get("GPTME_WEBUI_DEPLOY_WORKFLOW", "").strip()
    ref = os.environ.get("GPTME_WEBUI_DEPLOY_REF", "master").strip()
    token = os.environ.get("GPTME_WEBUI_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    enabled = _env_truthy("GPTME_WEBUI_ENABLE_DEV_DEPLOY")

    actions_url = (
        f"https://github.com/{repository}/actions/workflows/{workflow}"
        if workflow
        else f"https://github.com/{repository}/actions"
    )

    return {
        "enabled": enabled,
        "configured": enabled
        and bool(token)
        and "/" in repository
        and bool(workflow)
        and bool(ref),
        "repository": repository,
        "workflow": workflow,
        "ref": ref,
        "has_token": bool(token),
        "actions_url": actions_url,
    }


def _get_webui_deploy_inputs() -> dict[str, object] | None:
    raw_inputs = os.environ.get("GPTME_WEBUI_DEPLOY_INPUTS_JSON")
    if not raw_inputs:
        return None

    try:
        inputs = json.loads(raw_inputs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid GPTME_WEBUI_DEPLOY_INPUTS_JSON: {exc}") from exc

    if not isinstance(inputs, dict):
        raise ValueError("GPTME_WEBUI_DEPLOY_INPUTS_JSON must be a JSON object")

    return inputs


def _is_valid_image_content(path: "Path") -> bool:
    """Validate file content is a recognised image format using Pillow.

    Extension checks can be bypassed by renaming a file; this validates the
    actual content via magic-byte / header parsing.  Pillow is already a hard
    runtime dependency (needed for vision support), so no extra install cost.
    """
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(path) as img:
            _ = img.format  # triggers format detection from file content
        return True
    except (UnidentifiedImageError, FileNotFoundError, IsADirectoryError, OSError):
        return False
    except Exception:
        logger.warning("Unexpected error validating image %s", path, exc_info=True)
        return False


def _validate_model_input(model: str, expected_provider: str | None = None) -> str:
    """Validate a fully qualified provider/model identifier."""
    trimmed_model = model.strip()
    if not trimmed_model:
        raise ValueError("model must not be empty")
    if "/" not in trimmed_model:
        raise ValueError("model must be fully qualified as provider/model")

    provider, model_name = trimmed_model.split("/", 1)
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    if not model_name.strip():
        raise ValueError("model must include a model name after the provider prefix")
    if expected_provider and provider != expected_provider:
        raise ValueError(
            f"Model {trimmed_model} does not match provider {expected_provider}"
        )
    return trimmed_model


def _append_conversation_system_prompt(
    messages: list[Message], system_prompt: str | None
) -> None:
    """Append a conversation-local system prompt override when configured."""
    if system_prompt:
        messages.append(Message("system", system_prompt))


def _resolve_conversation_system_prompt(chat_config: ChatConfig) -> str | None:
    """Return the conversation prompt, falling back to the server default profile."""
    if chat_config.system_prompt:
        return chat_config.system_prompt

    server_default_profile = flask.current_app.config.get("SERVER_DEFAULT_PROFILE")
    if not server_default_profile:
        return None

    from ..profiles import get_profile

    profile = get_profile(server_default_profile)
    if profile and profile.system_prompt:
        return profile.system_prompt
    return None


def _generate_fork_conversation_id() -> str:
    """Generate a reasonably unique conversation id for forked copies."""
    timestamp = datetime.now(tz=timezone.utc).isoformat().replace(":", "-")
    return f"chat-{timestamp}-{time.time_ns() % 1_000_000_000:09d}"


def _fork_message_file_reference(
    file_ref: FilePath, source_attachments: Path, dest_attachments: Path
) -> tuple[FilePath, bool]:
    """Re-root attachment file paths into the forked conversation when needed."""
    if isinstance(file_ref, URI):
        return file_ref, False

    path = Path(file_ref)
    try:
        rel = path.relative_to(source_attachments)
        return dest_attachments / rel, True
    except ValueError:
        pass

    if path.parts[:1] == ("attachments",):
        return dest_attachments / Path(*path.parts[1:]), True

    return path, False


def _copy_messages_for_fork(
    messages: list[Message], source_logdir: Path, dest_logdir: Path
) -> list[Message]:
    """Copy a message slice into a new conversation, preserving attachments."""
    source_attachments = source_logdir / "attachments"
    dest_attachments = dest_logdir / "attachments"
    attachment_copies: set[tuple[Path, Path]] = set()
    copied_messages: list[Message] = []

    for msg in messages:
        new_files: list[FilePath] = []
        path_map: dict[str, str] = {}
        for file_ref in msg.files:
            new_ref, copied = _fork_message_file_reference(
                file_ref, source_attachments, dest_attachments
            )
            new_files.append(new_ref)
            if copied:
                assert isinstance(new_ref, Path)
                path_map[str(file_ref)] = str(new_ref)
                source_path = source_attachments / new_ref.relative_to(dest_attachments)
                attachment_copies.add((source_path, new_ref))

        new_file_hashes = {
            path_map.get(path, path): digest for path, digest in msg.file_hashes.items()
        }
        copied_messages.append(
            replace(msg, files=new_files, file_hashes=new_file_hashes)
        )

    for source_path, dest_path in attachment_copies:
        if not source_path.exists():
            continue
        if source_path.is_dir():
            shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)

    return copied_messages


def _get_optional_string_list_field(
    req_json: dict, field: str
) -> list[str] | None | tuple[flask.Response, int]:
    """Return an optional list[str] field or a 400 response."""
    value = req_json.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return flask.jsonify({"error": f"{field} must be a list of strings"}), 400
    return value


def _validate_message_file_references(
    files: list[str], workspace: Path
) -> list[FilePath] | tuple[flask.Response, int]:
    """Validate local message attachments stay within the conversation workspace."""
    file_paths: list[FilePath] = []
    for f_str in files:
        if is_uri(f_str):
            uri = parse_file_reference(f_str)
            assert isinstance(uri, URI)
            # file:// still points at local server paths and must not bypass workspace checks.
            if uri.scheme == "file":
                return (
                    flask.jsonify(
                        {
                            "error": "file:// URIs are not supported for message attachments"
                        }
                    ),
                    400,
                )
            file_paths.append(uri)
            continue

        f = Path(f_str)
        if f.is_absolute():
            return (
                flask.jsonify({"error": f"Absolute file paths are not supported: {f}"}),
                400,
            )

        full = (workspace / f).resolve()
        if not full.is_relative_to(workspace):
            return flask.jsonify({"error": f"File path escapes workspace: {f}"}), 400

        file_paths.append(full)
    return file_paths


def _persist_default_model(model: str) -> bool:
    """Persist the default model to [models].default and try to apply in-process.

    Returns True if a restart is still required to guarantee the change takes effect.
    """
    set_config_value("models.default", model, reload=False)

    model_meta = get_model(model)
    try:
        from gptme.llm import init_llm

        init_llm(cast(Provider, model_meta.provider))
        set_default_model(model_meta)
        flask.current_app.config["SERVER_DEFAULT_MODEL"] = model_meta
        return False
    except Exception:
        logger.warning(
            "Persisted default model %s but could not apply it in-process; restart required",
            model,
            exc_info=True,
        )
        return True


def _validate_api_key_input(api_key: str) -> str:
    """Apply lightweight sanity checks before persisting a user-supplied API key."""
    trimmed = api_key.strip()
    if not trimmed:
        raise ValueError("API key is empty")
    if len(trimmed) > 4096:
        raise ValueError("API key is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in trimmed):
        raise ValueError("API key contains control characters")
    return trimmed


# Sentinel substituted for secret values in GET /config-file responses.
_REDACT_SENTINEL = "***"

# Matches TOML section headers: [section] or [section.subsection]
_SECTION_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]")

# Key sub-patterns: bare (`API_KEY`), double-quoted (`"API_KEY"`), or single-quoted (`'API_KEY'`).
_SECRET_KEY_BARE = r"\b[\w]*(?:api_key|secret|password|token)\b"
_SECRET_KEY_DQUOTED = r'"[\w]*(?:api_key|secret|password|token)[\w]*"'
_SECRET_KEY_SQUOTED = r"'[\w]*(?:api_key|secret|password|token)[\w]*'"
_SECRET_KEY_ANY = rf"(?:{_SECRET_KEY_BARE}|{_SECRET_KEY_DQUOTED}|{_SECRET_KEY_SQUOTED})"

# Separate patterns for double-quoted and single-quoted TOML string values.
# Group 1: key + '= <quote>'  Group 2: value  Group 3: closing quote
_SECRET_KEY_DOUBLE_RE = re.compile(
    rf'(?i)({_SECRET_KEY_ANY}\s*=\s*")([^"]*?)(")',
)
_SECRET_KEY_SINGLE_RE = re.compile(
    rf"(?i)({_SECRET_KEY_ANY}\s*=\s*')([^']*?)(')",
)
_SECRET_KEY_PATTERNS = [_SECRET_KEY_DOUBLE_RE, _SECRET_KEY_SINGLE_RE]


def _redact_secrets(content: str) -> str:
    """Replace secret values in TOML config content with the redaction sentinel."""
    for pat in _SECRET_KEY_PATTERNS:
        content = pat.sub(rf"\g<1>{_REDACT_SENTINEL}\g<3>", content)
    return content


def _restore_redacted_secrets(incoming: str, original: str) -> str:
    """Restore sentinel placeholders with the real values from the on-disk file.

    When the webui reads a redacted config and then submits it back via PUT,
    any field that still holds the sentinel should preserve the real on-disk
    value rather than writing the placeholder.

    Uses (section, occurrence_index, key_name) as the lookup key so that
    repeated TOML array-of-tables (e.g. [[providers]]) restore each entry
    to its own correct secret rather than the last-seen value.
    """
    # Build section-scoped map of real secret values from the on-disk file.
    # Key: (section_header, occurrence_index, key_name)
    originals: dict[tuple[str, int, str], str] = {}
    section_counts: dict[str, int] = {}
    current_section = ""
    current_section_idx = 0
    for line in original.splitlines():
        sm = _SECTION_HEADER_RE.match(line)
        if sm:
            current_section = sm.group(1)
            current_section_idx = section_counts.get(current_section, 0)
            section_counts[current_section] = current_section_idx + 1
            continue
        for pat in _SECRET_KEY_PATTERNS:
            km = pat.search(line)
            if km:
                key = km.group(1).split("=")[0].strip().lower()
                originals[(current_section, current_section_idx, key)] = km.group(2)
                break

    def _make_restore(section: str, section_idx: int):
        def _restore(m: re.Match) -> str:
            key = m.group(1).split("=")[0].strip().lower()
            if m.group(2) == _REDACT_SENTINEL:
                real = originals.get((section, section_idx, key))
                if real is not None:
                    return m.group(1) + real + m.group(3)
            return m.group(0)

        return _restore

    result_lines: list[str] = []
    current_section = ""
    current_section_idx = 0
    section_counts = {}
    for line in incoming.splitlines(keepends=True):
        sm = _SECTION_HEADER_RE.match(line)
        if sm:
            current_section = sm.group(1)
            current_section_idx = section_counts.get(current_section, 0)
            section_counts[current_section] = current_section_idx + 1
            result_lines.append(line)
            continue
        for pat in _SECRET_KEY_PATTERNS:
            line = pat.sub(_make_restore(current_section, current_section_idx), line)
        result_lines.append(line)

    return "".join(result_lines)


def _get_user_config_file_response(content: str) -> dict[str, str | bool]:
    """Return raw config text with the same path metadata as user settings.

    Secret values (API keys etc.) are redacted with ``***`` before being
    included in the response — even when the auth bypass is active for
    local-only servers.
    """
    runtime_info = get_user_config_runtime_info()
    return {
        "content": _redact_secrets(content),
        "path": runtime_info["config_path"],
        "write_target": runtime_info["write_target"],
        "local_config_path": runtime_info["local_config_path"],
        "local_config_exists": runtime_info["local_config_exists"],
        "local_overrides_main": runtime_info["local_overrides_main"],
    }


def _read_user_config_file_text() -> str:
    """Read config.toml, creating the default file first if it is missing."""
    config_file, _local_path = get_user_config_paths()
    if not config_file.exists():
        load_user_config()
    return config_file.read_text()


def _validate_config_key_path(key: str) -> str:
    trimmed = key.strip()
    if not trimmed:
        raise ValueError("key must not be empty")
    if any(not segment.strip() for segment in trimmed.split(".")):
        raise ValueError("key must be a dotted path with non-empty segments")
    return trimmed


def _etag_conversations(conversations: "list[ConversationMeta]") -> str:
    """Compute a stable ETag from conversation IDs and modification times."""
    h = hashlib.md5()
    for c in conversations:
        h.update(f"{c.id}:{c.modified}\n".encode())
    return h.hexdigest()[:16]


def _etag_conversation(logdir: "Path") -> str:
    """Compute ETag from the conversation log file's mtime and size.

    Including size prevents stale 304s on filesystems with 1-second mtime
    granularity where two writes within the same tick leave mtime unchanged.
    """
    logfile = logdir / "conversation.jsonl"
    try:
        stat = logfile.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        return ""
    return hashlib.md5(f"{mtime}:{size}".encode()).hexdigest()[:16]


v2_api = flask.Blueprint("v2_api", __name__)

# Register sub-blueprints
v2_api.register_blueprint(sessions_api)
v2_api.register_blueprint(agents_api)


@v2_api.route("/api/v2/sessions")
@require_auth
@api_doc_simple(tags=["admin"])
def api_sessions_list():
    """List all active sessions.

    Returns a snapshot of all in-memory sessions with basic metadata useful
    for the admin panel (session ID, conversation ID, model, message count,
    generating status, elapsed time).
    """
    from ..dirs import get_logs_dir
    from .api_v2_sessions import SessionManager

    now = datetime.now(tz=timezone.utc)
    result = []
    for session_id, session in SessionManager.get_all_sessions():
        conversation_id = session.conversation_id

        # Count messages in the conversation log (best-effort; skip on error)
        message_count = 0
        model: str | None = None
        if conversation_id:
            try:
                manager = LogManager.load(conversation_id, lock=False)
                message_count = len(list(manager.log.messages))
                logdir = get_logs_dir() / conversation_id
                chat_config = ChatConfig.load_or_create(logdir, ChatConfig())
                model = chat_config.model
            except Exception:
                pass

        elapsed_seconds: float | None = None
        if session.generating and session.generating_since:
            elapsed_seconds = (now - session.generating_since).total_seconds()

        result.append(
            {
                "id": session_id,
                "conversation_id": conversation_id,
                "model": model,
                "message_count": message_count,
                "generating": session.generating,
                "elapsed_seconds": elapsed_seconds,
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
            }
        )

    return flask.jsonify(result)


@v2_api.route("/api/v2/sessions/<string:session_id>", methods=["DELETE"])
@require_auth
@api_doc_simple(tags=["admin"])
def api_session_delete(session_id: str):
    """Delete (kill) a session by ID.

    Interrupts any in-progress generation and removes the session from memory.
    The conversation log is not deleted.
    """
    from .api_v2_sessions import SessionManager

    session = SessionManager.get_session(session_id)
    if session is None:
        return flask.jsonify({"error": f"Session not found: {session_id}"}), 404

    # Mark as not generating before removal so the step thread exits cleanly
    session.generating = False
    session.generating_since = None
    session.pending_tools.clear()

    # Notify SSE clients before removing so they can react (avoids zombie streams)
    if session.conversation_id:
        SessionManager.add_event(session.conversation_id, {"type": "interrupted"})

    SessionManager.remove_session(session_id)
    return flask.jsonify({"status": "ok", "message": f"Session {session_id} deleted"})


@v2_api.route("/api/v2/server/health")
@require_auth
@api_doc_simple(tags=["admin"])
def api_server_health():
    """Server connection health summary.

    Returns a compact health snapshot: total session count, generating/idle
    breakdown, a per-slot strip, and a color-coded health indicator —
    without the per-session log-reading overhead of ``/api/v2/sessions``.

    Health levels:
    - ``green``  — no active generations or very light load
    - ``yellow`` — some sessions generating
    - ``red``    — all slots busy (every session is generating)
    """
    from .api_v2_sessions import SessionManager

    now = datetime.now(tz=timezone.utc)
    all_sessions = SessionManager.get_all_sessions()

    total = len(all_sessions)
    generating_count = 0
    slots = []

    for session_id, session in all_sessions:
        is_gen = bool(session.generating)
        if is_gen:
            generating_count += 1
        elapsed: float | None = None
        if is_gen and session.generating_since:
            elapsed = (now - session.generating_since).total_seconds()
        slots.append(
            {
                "id": session_id[:8],
                "generating": is_gen,
                "elapsed_seconds": elapsed,
            }
        )

    idle_count = total - generating_count

    if total == 0 or generating_count == 0:
        health = "green"
    elif generating_count < total:
        health = "yellow"
    else:
        health = "red"

    return flask.jsonify(
        {
            "session_count": total,
            "generating_count": generating_count,
            "idle_count": idle_count,
            "health": health,
            "slots": slots,
        }
    )


@v2_api.route("/api/v2")
@api_doc_simple(responses={200: ApiRootResponse}, tags=["meta"])
def api_root():
    """V2 API root.

    Get information about the v2 API, including available endpoints and capabilities.
    """
    provider = get_external_session_provider()
    capabilities = {
        "external_session_catalog": False,
        "external_session_transcript": False,
    }
    if provider is not None:
        capabilities.update(provider.capabilities)

    provider_configured = get_default_model() is not None

    body = {
        "message": "gptme v2 API",
        "documentation": "https://gptme.org/docs/server.html",
        "version": __version__,
        "api_version": API_VERSION,
        "contract_revision": CONTRACT_REVISION,
        "capabilities": capabilities,
        "provider_configured": provider_configured,
    }
    response = flask.jsonify(body)
    response.headers["X-API-Version"] = str(API_VERSION)
    return response


@v2_api.route("/api/v2/version")
@api_doc_simple(responses={200: ApiVersionResponse}, tags=["meta"])
def api_version():
    """Get the current API version.

    Lightweight alternative to the full /api/v2 root for clients that only
    need to check version compatibility.
    """
    response = flask.jsonify(
        {"api_version": API_VERSION, "contract_revision": CONTRACT_REVISION}
    )
    response.headers["X-API-Version"] = str(API_VERSION)
    return response


@v2_api.route("/api/v2/config")
@api_doc()
def api_config():
    """Agent configuration endpoint.

    Returns workspace agent configuration including named URLs (dashboard, repo, etc.)
    configured in ``gptme.toml`` under ``[agent.urls]``.

    Clients can use the ``dashboard`` URL to link to or embed the agent's dashboard.

    Response schema::

        {
          "agent": {
            "name": "bob",               // agent name from gptme.toml [agent] name
            "urls": {                     // from [agent.urls] section (may be empty)
              "dashboard": "https://...", // static dashboard URL (gh-pages)
              "dashboard-api": "https://..." // live dashboard API URL (optional)
            }
          }
        }
    """
    config = get_config()
    agent = config.project.agent if config.project else None

    agent_info: dict = {}
    if agent:
        agent_info["name"] = agent.name
        if agent.urls:
            agent_info["urls"] = agent.urls

    return flask.jsonify({"agent": agent_info})


@v2_api.route("/api/v2/dev/deploy-staging", methods=["GET"])
@require_auth
def api_dev_deploy_staging_status():
    """Return whether the web UI staging deploy trigger is configured."""
    return flask.jsonify(_get_webui_deploy_config())


@v2_api.route("/api/v2/dev/deploy-staging", methods=["POST"])
@require_auth
def api_dev_deploy_staging_trigger():
    """Trigger the configured web UI staging deploy workflow."""
    config = _get_webui_deploy_config()

    if not config["enabled"]:
        return (
            flask.jsonify(
                {
                    "error": "Web UI deploy trigger is disabled",
                    "detail": "Set GPTME_WEBUI_ENABLE_DEV_DEPLOY=true on the gptme server.",
                }
            ),
            403,
        )

    token = os.environ.get("GPTME_WEBUI_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return (
            flask.jsonify(
                {
                    "error": "Web UI deploy trigger is not configured",
                    "detail": "Set GPTME_WEBUI_GITHUB_TOKEN with Actions workflow permissions.",
                }
            ),
            503,
        )

    repository = str(config["repository"])
    workflow_name = str(config["workflow"])
    ref = str(config["ref"])
    if "/" not in repository:
        return (
            flask.jsonify(
                {
                    "error": "Invalid GPTME_WEBUI_DEPLOY_REPOSITORY",
                    "detail": "Expected owner/repo format.",
                }
            ),
            500,
        )
    if not workflow_name:
        return (
            flask.jsonify(
                {
                    "error": "GPTME_WEBUI_DEPLOY_WORKFLOW is required",
                    "detail": "Set it to a workflow file that supports workflow_dispatch.",
                }
            ),
            503,
        )
    if not ref:
        return (
            flask.jsonify(
                {
                    "error": "GPTME_WEBUI_DEPLOY_REF is required",
                    "detail": "Set it to the branch or tag to deploy.",
                }
            ),
            503,
        )

    try:
        payload: dict[str, object] = {"ref": ref}
        inputs = _get_webui_deploy_inputs()
        if inputs:
            payload["inputs"] = inputs
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 500

    owner, repo_name = repository.split("/", 1)
    encoded_repository = (
        f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo_name, safe='')}"
    )
    workflow = urllib.parse.quote(workflow_name, safe="")
    api_url = f"https://api.github.com/repos/{encoded_repository}/actions/workflows/{workflow}/dispatches"
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "gptme-webui-dev-deploy",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 204:
                return (
                    flask.jsonify(
                        {
                            "error": "GitHub did not accept the deploy request",
                            "detail": f"Unexpected status {response.status}",
                        }
                    ),
                    502,
                )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return (
            flask.jsonify(
                {
                    "error": "GitHub rejected the deploy request",
                    "github_status": exc.code,
                    "detail": detail,
                }
            ),
            502,
        )
    except urllib.error.URLError as exc:
        return (
            flask.jsonify(
                {
                    "error": "Could not reach GitHub",
                    "detail": str(exc.reason),
                }
            ),
            502,
        )

    return (
        flask.jsonify(
            {
                "status": "queued",
                "message": "Staging deploy workflow queued",
                "repository": repository,
                "workflow": workflow_name,
                "ref": ref,
                "actions_url": config["actions_url"],
            }
        ),
        202,
    )


@v2_api.route("/api/v2/external-sessions")
@require_auth
@api_doc_simple(
    responses={200: ExternalSessionListResponse, 503: ErrorResponse},
    tags=["external-sessions"],
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 100},
            "description": "Maximum number of external sessions to return",
        },
        {
            "name": "days",
            "in": "query",
            "schema": {"type": "integer", "default": 7},
            "description": "How many recent days of session history to scan",
        },
    ],
)
def api_external_sessions():
    """List read-only external sessions discovered by the server."""
    provider = get_external_session_provider()
    if provider is None:
        return flask.jsonify({"error": "external session provider unavailable"}), 503

    try:
        limit = int(request.args.get("limit", 100))
        days = int(request.args.get("days", 7))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "limit and days must be integers"}), 400

    if limit <= 0:
        return flask.jsonify({"error": "limit must be a positive integer"}), 400
    limit = min(limit, 1000)
    if days <= 0:
        return flask.jsonify({"error": "days must be a positive integer"}), 400
    days = min(days, 365)

    sessions = [
        item.to_dict() for item in provider.list_sessions(limit=limit, days=days)
    ]
    return flask.jsonify({"sessions": sessions})


@v2_api.route("/api/v2/external-sessions/<string:external_session_id>")
@require_auth
@api_doc_simple(
    responses={200: ExternalSessionResponse, 404: ErrorResponse, 503: ErrorResponse},
    tags=["external-sessions"],
    parameters=[
        {
            "name": "external_session_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Opaque external session identifier",
        },
        {
            "name": "days",
            "in": "query",
            "schema": {"type": "integer", "default": 7},
            "description": "How many recent days of session history to scan",
        },
    ],
)
def api_external_session(external_session_id: str):
    """Get a normalized read-only external session transcript."""
    try:
        days = int(request.args.get("days", 7))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "days must be an integer"}), 400
    if days <= 0:
        return flask.jsonify({"error": "days must be a positive integer"}), 400
    days = min(days, 365)

    provider = get_external_session_provider()
    if provider is None:
        return flask.jsonify({"error": "external session provider unavailable"}), 503

    session = provider.get_session(external_session_id, days=days)
    if session is None:
        return flask.jsonify(
            {"error": f"External session not found: {external_session_id}"}
        ), 404
    return flask.jsonify(session)


@v2_api.route(
    "/api/v2/external-sessions/<string:external_session_id>/steer", methods=["POST"]
)
@require_auth
@api_doc_simple(
    responses={
        200: ExternalSessionSteerResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        501: ErrorResponse,
        503: ErrorResponse,
    },
    tags=["external-sessions"],
    parameters=[
        {
            "name": "external_session_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Opaque external session identifier",
        },
        {
            "name": "days",
            "in": "query",
            "schema": {"type": "integer", "default": 7},
            "description": "How many recent days of session history to scan",
        },
    ],
)
def api_steer_external_session(external_session_id: str):
    """Inject a user message into a running external session (steer_inject).

    Only works for sessions that advertise ``steer_inject`` in their capabilities.
    Returns 422 if the session does not support steering, 501 if the server-side
    steer implementation (gptme-sessions steer) is not available.
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return flask.jsonify({"error": "message is required"}), 400

    try:
        days = int(request.args.get("days", 7))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "days must be an integer"}), 400
    if days <= 0:
        return flask.jsonify({"error": "days must be a positive integer"}), 400
    days = min(days, 365)

    provider = get_external_session_provider()
    if provider is None:
        return flask.jsonify({"error": "external session provider unavailable"}), 503

    session = provider.get_session(external_session_id, days=days)
    if session is None:
        return flask.jsonify(
            {"error": f"External session not found: {external_session_id}"}
        ), 404

    capabilities = session.get("transcript", {}).get("capabilities") or []
    if "steer_inject" not in capabilities:
        return flask.jsonify({"error": "session does not support steer_inject"}), 422

    trajectory_path = session.get("transcript", {}).get("trajectory_path")
    if not trajectory_path:
        return flask.jsonify({"error": "session has no trajectory_path"}), 500

    try:
        ok = provider.steer_session(str(trajectory_path), message)
    except Exception as exc:
        logger.exception("steer_session failed for %s", external_session_id)
        return flask.jsonify({"error": f"steer_session error: {exc}"}), 500

    if not ok:
        return flask.jsonify(
            {
                "error": (
                    "steer_inject not available — upgrade gptme-sessions "
                    "to a version that includes the 'steer' subcommand"
                )
            }
        ), 501

    return flask.jsonify({"status": "ok"})


@v2_api.route("/api/v2/conversations")
@require_auth
@api_doc_simple(
    responses={200: ConversationListResponse, 500: ErrorResponse},
    tags=["conversations-v2"],
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 100},
            "description": "Maximum number of conversations to return",
        },
        {
            "name": "q",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Filter conversations by name, id, or last message preview (case-insensitive substring match). Alias: search.",
        },
        {
            "name": "detail",
            "in": "query",
            "schema": {"type": "boolean", "default": False},
            "description": "If true, perform a full scan to populate cost and token stats. Slower but returns accurate total_cost, total_input_tokens, and total_output_tokens fields. Suitable for paginated views; avoid on large collections. When combined with search, every conversation is fully scanned before the limit is applied — avoid on large deployments.",
        },
        {
            "name": "cursor",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Opaque pagination cursor returned by the previous paginated response. New clients should pass it through unchanged; legacy numeric Unix timestamp cursors are still accepted.",
        },
        {
            "name": "paginated",
            "in": "query",
            "schema": {"type": "boolean", "default": False},
            "description": "If true, return a paginated response object {conversations: [...], next_cursor: string|null} instead of a bare list. Use cursor param for page 2+.",
        },
    ],
)
def api_conversations():
    """List conversations (V2).

    Get a list of user conversations with metadata using the V2 API.
    Supports optional search filtering by conversation name, id, or last message preview.
    Pass ``detail=true`` to include cost/token stats (slower full scan).

    Each item exposes ``id``, ``name``, ``created``, ``modified``,
    ``message_count`` (alias of ``messages``), ``last_updated`` (alias of
    ``modified``), and a ``last_message_preview`` for the most recent
    user/assistant turn. ``message_count`` and ``last_updated`` are always
    populated (cheap fast-tail scan), so list-level UIs can render a stats
    badge without per-row detail fetches.

    Pass ``paginated=1`` to opt into cursor pagination: the response becomes
    ``{conversations: [...], next_cursor: string|null}``. Pass the returned
    cursor back unchanged on page 2+.
    """
    try:
        limit = int(request.args.get("limit", 100))
    except (ValueError, TypeError):
        return flask.jsonify({"error": "limit must be an integer"}), 400
    if limit <= 0:
        return flask.jsonify({"error": "limit must be a positive integer"}), 400
    limit = min(limit, 1000)
    # ?q= is the primary filter param; ?search= is accepted as a backward-compat alias
    # Use None-check so explicit ?q= (even empty) takes precedence over ?search=
    q = request.args.get("q")
    search = (q if q is not None else request.args.get("search", "")).strip().lower()
    detail_val = request.args.get("detail")
    detail = detail_val is not None and detail_val.lower() in ("", "1", "true", "yes")
    paginated_val = request.args.get("paginated")
    paginated = paginated_val is not None and paginated_val.lower() in (
        "",
        "1",
        "true",
        "yes",
    )
    cursor_raw = request.args.get("cursor")
    cursor_ts: float | None = None
    cursor_id: str | None = None
    if cursor_raw:
        try:
            cursor_ts, cursor_id = _parse_conversation_cursor(cursor_raw)
        except (ValueError, TypeError):
            return flask.jsonify(
                {
                    "error": "cursor must be a numeric Unix timestamp or opaque cursor token"
                }
            ), 400

    global \
        _conversations_cache, \
        _conversations_cache_logs_dir, \
        _conversations_cache_time
    logs_dir = conversations_module.get_logs_dir()

    # Cursor pagination path — returns {conversations: [...], next_cursor: string|null}
    if paginated:
        # Load full list (use cache when possible for the common non-search, non-detail case)
        full_list: list[ConversationMeta]
        if not search and not detail:
            _cached = _conversations_cache
            if (
                _cached is not None
                and _conversations_cache_logs_dir == logs_dir
                and (time.monotonic() - _conversations_cache_time)
                < _CONVERSATIONS_CACHE_TTL
            ):
                full_list = _cached
                record_cache_result(hit=True)
            else:
                full_list = list(get_user_conversations(detail=False))
                _conversations_cache = full_list
                _conversations_cache_logs_dir = logs_dir
                _conversations_cache_time = time.monotonic()
                record_cache_result(hit=False)
                n_msgs = sum(c.messages for c in full_list)
                update_conversation_metrics(len(full_list), n_msgs)
        elif search:
            full_list = [
                conv
                for conv in get_user_conversations(detail=detail)
                if (
                    search in conv.name.lower()
                    or search in conv.id.lower()
                    or search in (conv.last_message_preview or "").lower()
                )
            ]
        else:
            full_list = list(get_user_conversations(detail=detail))

        # Keep pagination deterministic even when multiple conversations share
        # the same modified timestamp.
        full_list = sorted(
            full_list, key=lambda conv: (conv.modified, conv.id), reverse=True
        )

        # Apply cursor filter: composite cursors paginate on (modified, id),
        # legacy numeric cursors fall back to timestamp-only filtering.
        if cursor_ts is not None:
            if cursor_id is None:
                paged = [c for c in full_list if c.modified < cursor_ts]
            else:
                paged = [
                    c for c in full_list if (c.modified, c.id) < (cursor_ts, cursor_id)
                ]
        else:
            paged = full_list

        # Fetch limit+1 to detect whether a next page exists
        probe = paged[: limit + 1]
        has_more = len(probe) > limit
        page = probe[:limit]

        page_items = []
        for conv in page:
            item = asdict(conv)
            item["message_count"] = conv.messages
            item["last_updated"] = conv.modified
            page_items.append(item)

        next_cursor: str | None = (
            _encode_conversation_cursor(page[-1]) if has_more and page else None
        )

        return flask.jsonify({"conversations": page_items, "next_cursor": next_cursor})

    # Legacy path — returns bare list (backward compatible)
    # Use cached list for the common case (no search, no detail).
    # Cache is invalidated on conversation create/update/delete and scoped to
    # the current logs dir so test/workspace swaps do not reuse stale results.
    if (
        not search
        and not detail
        and _conversations_cache is not None
        and _conversations_cache_logs_dir == logs_dir
    ):
        elapsed = time.monotonic() - _conversations_cache_time
        if elapsed < _CONVERSATIONS_CACHE_TTL:
            cached = _conversations_cache
            record_cache_result(hit=True)
            etag = _etag_conversations(cached[:limit])
            if request.if_none_match.contains_weak(etag):
                resp = flask.make_response("", 304)
                resp.set_etag(etag, weak=True)
                resp.cache_control.no_cache = True
                return resp
            response_items = [asdict(c) for c in cached[:limit]]
            for item in response_items:
                item["message_count"] = item["messages"]
                item["last_updated"] = item["modified"]
            resp = flask.make_response(flask.jsonify(response_items))
            resp.set_etag(etag, weak=True)
            resp.cache_control.no_cache = True
            return resp

    # Use fast tail-only scan for list/search by default — reads last 8KB for
    # preview/model, skips json.loads() on every metadata line.
    # Pass detail=true to opt in to full cost/token stats (slower).
    if search:
        conversations = []
        for conv in get_user_conversations(detail=detail):
            if (
                search in conv.name.lower()
                or search in conv.id.lower()
                or search in (conv.last_message_preview or "").lower()
            ):
                conversations.append(conv)
                if len(conversations) >= limit:
                    break
    else:
        # The API limit is capped at 1000, so a cold-cache fill does not need
        # to scan beyond that bound.
        all_conversations = list(islice(get_user_conversations(detail=detail), 1000))
        conversations = all_conversations[:limit]
    response_items = []
    for conv in conversations:
        item = asdict(conv)
        # `messages` is the message count (int), not message content — both
        # the fast tail-scan and full scan populate it, so it is safe to keep
        # in the response regardless of `detail`.
        # Add stable webui-facing aliases: `message_count` and `last_updated`.
        item["message_count"] = conv.messages
        item["last_updated"] = conv.modified
        response_items.append(item)

    if not search and not detail:
        _conversations_cache = all_conversations
        _conversations_cache_logs_dir = logs_dir
        _conversations_cache_time = time.monotonic()
        record_cache_result(hit=False)
        n_msgs = sum(c.messages for c in all_conversations)
        update_conversation_metrics(len(all_conversations), n_msgs)
        etag = _etag_conversations(conversations)
        if request.if_none_match.contains_weak(etag):
            resp = flask.make_response("", 304)
            resp.set_etag(etag, weak=True)
            resp.cache_control.no_cache = True
            return resp
        resp = flask.make_response(flask.jsonify(response_items))
        resp.set_etag(etag, weak=True)
        resp.cache_control.no_cache = True
        return resp

    return flask.jsonify(response_items)


@v2_api.route("/api/v2/conversations/<string:conversation_id>")
@require_auth
@api_doc_simple(
    responses={200: ConversationResponse, 404: ErrorResponse}, tags=["conversations-v2"]
)
def api_conversation(conversation_id: str):
    """Get conversation (V2).

    Retrieve a conversation with all its messages and metadata using the V2 API.

    Supports optional message pagination:
    - ``limit=N`` returns only the last N messages (default: all messages).
    - ``before=<index>`` combined with ``limit`` returns the N messages ending at
      ``<index>`` (exclusive), enabling "load older" pagination.  When ``limit``
      is set, the response includes ``total_messages``, ``has_more``, and
      ``before`` (the cursor for the next older page when ``has_more`` is true).
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    # Parse pagination params early for fast-fail on bad input
    try:
        limit = int(request.args["limit"]) if "limit" in request.args else None
    except (ValueError, TypeError):
        return flask.jsonify({"error": "limit must be a positive integer"}), 400
    if limit is not None and limit <= 0:
        return flask.jsonify({"error": "limit must be a positive integer"}), 400
    if limit is not None:
        limit = min(limit, 10000)

    try:
        before = int(request.args["before"]) if "before" in request.args else None
    except (ValueError, TypeError):
        return flask.jsonify({"error": "before must be a non-negative integer"}), 400
    if before is not None and before < 0:
        return flask.jsonify({"error": "before must be a non-negative integer"}), 400
    if before is not None and limit is None:
        return flask.jsonify({"error": "before requires limit to be set"}), 400

    logdir = get_logs_dir() / conversation_id
    etag = _etag_conversation(logdir)
    # Pagination params change the response body, so incorporate them into the ETag.
    etag_paged = f"{etag}-{limit}-{before}" if (etag and limit is not None) else etag
    if etag_paged and request.if_none_match.contains_weak(etag_paged):
        resp = flask.make_response("", 304)
        resp.set_etag(etag_paged, weak=True)
        resp.cache_control.no_cache = True
        return resp

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return flask.jsonify(
            {"error": f"Conversation not found: {conversation_id}"}
        ), 404

    # Create and set config
    chat_config = ChatConfig.load_or_create(logdir, ChatConfig()).save()
    log_dict = manager.to_dict(branches=True)
    log_dict["logdir"] = str(logdir)

    # make all paths relative to workspace or logdir (no "../" or absolute paths)
    for msg in log_dict["log"]:
        if files := msg.get("files"):
            msg["files"] = [
                _abs_to_rel_workspace(f, chat_config.workspace, manager.logdir)
                for f in files
            ]

    # Apply message pagination when limit is specified.
    # Only the active-branch log is paginated; branches dict is left intact
    # (used for branch switching, not message display).
    if limit is not None:
        all_messages = log_dict["log"]
        total = len(all_messages)
        end = min(before, total) if before is not None else total
        end = max(0, end)
        start = max(0, end - limit)
        log_dict["log"] = all_messages[start:end]
        log_dict["total_messages"] = total
        log_dict["has_more"] = start > 0
        if start > 0:
            log_dict["before"] = start  # cursor for the next older page

    # Include agent info if available
    agent_config = chat_config.agent_config
    if agent_config:
        log_dict["agent"] = {
            "name": agent_config.name,
            "avatar": agent_config.avatar,
            "urls": agent_config.urls or None,
        }
        if chat_config.agent:
            log_dict["agent"]["path"] = str(chat_config.agent)

    # Surface session-level state so REST polling clients can see generation
    # status and the last step error without subscribing to SSE.
    # Errors during /step propagate via SSE error events, but clients that
    # only poll the conversation would otherwise see {"status": "ok"} from
    # /step and never learn the LLM call failed.
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    if sessions:
        # Pick the most recently active session — it's the one a polling
        # client would care about.
        latest = max(sessions, key=lambda s: s.last_activity)
        log_dict["session"] = {
            "id": latest.id,
            "generating": latest.generating,
            "last_error": latest.last_error,
        }

    resp = flask.make_response(flask.jsonify(log_dict))
    if etag_paged:
        resp.set_etag(etag_paged, weak=True)
        resp.cache_control.no_cache = True
    return resp


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["PUT"])
@require_auth
@api_doc(
    summary="Create conversation (V2)",
    description="Create a new conversation with initial configuration and messages using the V2 API",
    request_body=ConversationCreateRequest,
    responses={200: SessionResponse, 409: ErrorResponse, 400: ErrorResponse},
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_put(conversation_id: str):
    """Create a new conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id

    req_json = request.get_json(silent=True)
    if req_json is None:
        if request.get_data(cache=True):
            return flask.jsonify({"error": "Malformed JSON in request body"}), 400
        req_json = {}
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    # Validate auto_confirm type before any side effects (CWE-20: truthy coercion).
    # "false" (string) is truthy in Python — must reject non-bool/int values.
    # Same pattern as api_v2_sessions.py.
    auto_confirm = req_json.get("auto_confirm", False)
    if type(auto_confirm) not in (bool, int):
        return (
            flask.jsonify(
                {
                    "error": "Invalid 'auto_confirm' value",
                    "message": "'auto_confirm' must be a boolean or integer",
                }
            ),
            400,
        )

    # Validate all messages before creating any side effects (directories).
    # This prevents orphaned directories when validation fails: if logdir.mkdir()
    # runs before a 400 is returned, the same conversation_id gets a 409 on retry.
    messages_raw = req_json.get("messages", [])
    if not isinstance(messages_raw, list):
        return flask.jsonify({"error": "'messages' must be a list"}), 400
    _RoleType = Literal["system", "user", "assistant"]
    valid_roles = ("system", "user", "assistant")
    validated_msgs: list[tuple[_RoleType, str, datetime, list[str]]] = []
    for msg in messages_raw:
        if not isinstance(msg, dict):
            return flask.jsonify({"error": "Each message must be an object"}), 400
        if msg.get("role") not in valid_roles:
            return (
                flask.jsonify(
                    {
                        "error": f"Invalid role: {msg.get('role')}. Must be one of: {valid_roles}"
                    }
                ),
                400,
            )
        if "content" not in msg:
            return flask.jsonify(
                {"error": "Message missing required 'content' field"}
            ), 400
        if not isinstance(msg["content"], str):
            return flask.jsonify({"error": "Message 'content' must be a string"}), 400
        if "timestamp" in msg:
            try:
                ts: datetime = isoparse(msg["timestamp"])
            except (ValueError, OverflowError, TypeError):
                return flask.jsonify(
                    {"error": f"Invalid timestamp format: {msg['timestamp']}"}
                ), 400
        else:
            ts = datetime.now(tz=timezone.utc)
        files_raw = msg.get("files", [])
        if not isinstance(files_raw, list) or not all(
            isinstance(f, str) for f in files_raw
        ):
            return flask.jsonify(
                {"error": "Message 'files' must be a list of strings"}
            ), 400
        validated_msgs.append(
            (cast(_RoleType, msg["role"]), msg["content"], ts, files_raw)
        )

    config_raw = req_json.get("config", {})
    if not isinstance(config_raw, dict):
        return flask.jsonify({"error": "'config' must be an object"}), 400
    prompt = req_json.get("prompt", "full")
    if not isinstance(prompt, str):
        return flask.jsonify({"error": "'prompt' must be a string"}), 400

    # Load or create the chat config, overriding values from request config if provided
    config_dict = dict(config_raw)
    config_dict["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    # Server sessions default to an isolated per-conversation workspace so they
    # don't inherit the server process's cwd.  Clients can override by passing an
    # explicit workspace in the request config.
    config_dict.setdefault("chat", {})
    if not isinstance(config_dict["chat"], dict):
        return flask.jsonify({"error": "'config.chat' must be an object"}), 400
    config_dict["chat"].setdefault("workspace", "@log")
    try:
        request_config = ChatConfig.from_dict(config_dict, create_workspace=False)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    # Enforce workspace containment: server clients must not direct the agent
    # outside the conversation's logdir (path traversal / SSRF via workspace).
    # Resolve logdir to handle symlinks (e.g. macOS /tmp -> /private/tmp).
    if not request_config.workspace.is_relative_to(logdir.resolve()):
        return flask.jsonify({"error": "workspace escapes conversation logdir"}), 400

    # Create the log directory atomically to avoid TOCTOU race
    try:
        logdir.mkdir(parents=True)
    except FileExistsError:
        return (
            flask.jsonify({"error": f"Conversation already exists: {conversation_id}"}),
            409,
        )

    chat_config = ChatConfig.load_or_create(logdir, request_config)

    msgs = list(
        get_prompt(
            tools=list(get_toolchain(chat_config.tools, strict=False)),
            interactive=chat_config.interactive,
            tool_format=chat_config.tool_format or "markdown",
            model=chat_config.model,
            prompt=prompt,
            workspace=chat_config.workspace,
            agent_path=chat_config.agent,
            initial_prompt=next(
                (content for role, content, _, _ in validated_msgs if role == "user"),
                None,
            ),
        )
    )

    # Inject output-format capability hint for webui mode
    # gptme-webui renders ```html code blocks in a sandboxed iframe with
    # Code/Preview tabs — inform the model so it can use HTML when it provides
    # a richer experience for the reader.
    # Set GPTME_SERVE_HTML_HINT=false to disable for non-webui API clients.
    if (
        os.environ.get("GPTME_SERVE_HTML_HINT", "true").lower() != "false"
        and prompt != "none"
    ):
        msgs.append(
            Message(
                "system",
                "Output format: you are in a web interface. "
                "For interactive content (dashboards, tables, diagrams), "
                "use ```html blocks — they render live in a sandboxed preview panel.",
            )
        )

    resolved_prompt = _resolve_conversation_system_prompt(chat_config)
    # Persist the resolved prompt back to chat_config so it survives server
    # restarts that don't pass --default-profile (fixes Greptile P1 durability gap).
    if resolved_prompt and not chat_config.system_prompt:
        chat_config.system_prompt = resolved_prompt
    _append_conversation_system_prompt(msgs, resolved_prompt)

    for role, content, timestamp, files_raw in validated_msgs:
        file_paths: list[FilePath] = []
        if files_raw:
            validated_files = _validate_message_file_references(
                files_raw, chat_config.workspace
            )
            if isinstance(validated_files, tuple):
                shutil.rmtree(logdir, ignore_errors=True)
                return validated_files
            file_paths = validated_files
        msgs.append(Message(role, content, timestamp=timestamp, files=file_paths))

    log = LogManager.load(logdir=logdir, initial_msgs=msgs, create=True)
    log.write()

    # Set tool allowlist to available tools if not provided
    if not chat_config.tools:
        chat_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]

    if not chat_config.mcp:
        # load from user or project config
        config = Config.from_workspace(chat_config.workspace)
        chat_config.mcp = config.mcp

    # Save the chat config
    chat_config.save()

    # Create a session for this conversation
    session = SessionManager.create_session(conversation_id)

    # Set auto_confirm_count from the already-validated auto_confirm value
    if type(auto_confirm) is bool:
        if auto_confirm:
            session.auto_confirm_count = 999
    elif auto_confirm > 0:
        session.auto_confirm_count = auto_confirm

    _invalidate_conversations_cache()
    return flask.jsonify(
        {"status": "ok", "conversation_id": conversation_id, "session_id": session.id}
    )


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["POST"])
@require_auth
@api_doc(
    summary="Add message to conversation (V2)",
    description="Add a new message to an existing conversation using the V2 API",
    request_body=MessageCreateRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
    parameters=[CONVERSATION_ID_PARAM],
    tags=["conversations-v2"],
)
def api_conversation_post(conversation_id: str):
    """Append a message to a conversation."""
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    if "role" not in req_json or "content" not in req_json:
        return flask.jsonify({"error": "Missing required fields (role, content)"}), 400

    # Validate field types
    if not isinstance(req_json["content"], str):
        return flask.jsonify({"error": "content must be a string"}), 400

    # Validate role against allowed values
    valid_roles = ("system", "user", "assistant")
    if req_json["role"] not in valid_roles:
        return (
            flask.jsonify(
                {
                    "error": f"Invalid role: {req_json['role']}. Must be one of: {valid_roles}"
                }
            ),
            400,
        )

    branch = req_json.get("branch", "main")
    if error := _validate_branch(branch):
        return error
    tool_allowlist = _get_optional_string_list_field(req_json, "tools")
    if isinstance(tool_allowlist, tuple):
        return tool_allowlist

    try:
        init_tools(tool_allowlist)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return flask.jsonify({"error": f"Failed to load tool: {exc}"}), 400

    # Check conversation existence before branch to return accurate error messages.
    logdir = get_logs_dir() / conversation_id
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )
    try:
        log = LogManager.load(logdir, branch=branch)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Branch not found: {branch}"}),
            404,
        )

    # Validate and convert file paths from JSON strings to Path objects
    files_result = _get_optional_string_list_field(req_json, "files")
    if isinstance(files_result, tuple):
        return files_result
    file_paths: list[FilePath] = []
    if files_result is not None:
        validated_files = _validate_message_file_references(files_result, log.workspace)
        if isinstance(validated_files, tuple):
            return validated_files
        file_paths = validated_files
    msg = Message(
        req_json["role"],
        req_json["content"],
        files=file_paths,
    )

    # Check if the message is a slash command (e.g. /help, /model, /tools)
    if msg.role == "user" and is_message_command(msg.content):
        # Block commands that are unsafe in server context (see _SERVER_BLOCKED_COMMANDS)
        parts = msg.content.lstrip("/").split()
        cmd_name = parts[0] if parts else ""
        if cmd_name in _SERVER_BLOCKED_COMMANDS:
            return flask.jsonify(
                {"error": f"Command /{cmd_name} is not available in server mode"}
            ), 400

        # Append command message first (handle_cmd may undo it via auto_undo)
        log.append(msg)
        SessionManager.add_event(
            conversation_id,
            {
                "type": "message_added",
                "message": msg2dict(msg, log.workspace, log.logdir),
            },
        )

        # Execute the command and collect response messages
        responses: list[Message] = []
        try:
            for resp in handle_cmd(msg.content, log):
                log.append(resp)
                responses.append(resp)
                SessionManager.add_event(
                    conversation_id,
                    {
                        "type": "message_added",
                        "message": msg2dict(resp, log.workspace, log.logdir),
                    },
                )
        except Exception as e:
            logger.exception("Error executing command: %s", msg.content)
            error_msg = Message("system", f"Command error: {e}")
            log.append(error_msg)
            responses.append(error_msg)
            SessionManager.add_event(
                conversation_id,
                {
                    "type": "message_added",
                    "message": msg2dict(error_msg, log.workspace, log.logdir),
                },
            )

        return flask.jsonify(
            {"status": "ok", "command": True, "responses": len(responses)}
        )

    log.append(msg)

    # Notify all sessions that a new message was added
    SessionManager.add_event(
        conversation_id,
        {
            "type": "message_added",
            "message": msg2dict(msg, log.workspace, log.logdir),
        },
    )

    # Partial cache update: only refresh this conversation's entry so the
    # conversations-list endpoint can keep serving from cache during streaming.
    _update_conversation_in_cache(conversation_id)
    return flask.jsonify({"status": "ok"})


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/messages/<int:index>",
    methods=["PATCH"],
)
@require_auth
@api_doc(
    summary="Edit message in conversation (V2)",
    description="Edit a user message in a conversation. With ?truncate=1, removes all messages after the edited one.",
    responses={
        200: ConversationResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
    parameters=[
        CONVERSATION_ID_PARAM,
        {
            "name": "index",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
            "description": "Message index",
        },
        {
            "name": "truncate",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["0", "1"]},
            "description": "If '1', remove all messages after the edited one",
        },
    ],
    tags=["conversations-v2"],
)
def api_conversation_edit_message(conversation_id: str, index: int):
    """Edit a message in a conversation.

    Only user messages can be edited. Creates a backup branch before editing.
    With ?truncate=1, removes all messages after the edited one.
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = request.get_json(silent=True)
    if req_json is None:
        if request.get_data(cache=True):
            return flask.jsonify({"error": "Malformed JSON in request body"}), 400
        req_json = {}
    elif not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    content = req_json.get("content")
    files = req_json.get("files")  # Optional list of file paths
    truncate = request.args.get("truncate") == "1"

    if content is not None and not isinstance(content, str):
        return flask.jsonify({"error": "content must be a string"}), 400
    if files is not None and (
        not isinstance(files, list) or not all(isinstance(f, str) for f in files)
    ):
        return flask.jsonify({"error": "files must be a list of strings"}), 400

    # Content or files required for edits, but optional for pure truncation
    has_changes = (content and content.strip()) or files is not None
    if not truncate and not has_changes:
        return flask.jsonify({"error": "content or files is required"}), 400

    # Check if generation is in progress
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify({"error": "Cannot edit while generation is in progress"}),
                409,
            )

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    msgs = list(manager.log.messages)
    if index < 0 or index >= len(msgs):
        return (
            flask.jsonify(
                {"error": f"Message index {index} out of range (0-{len(msgs) - 1})"}
            ),
            404,
        )

    # Content/file changes are only allowed on user messages.
    # Truncation (without content change) is allowed on any message.
    content_changed = content is not None and content != msgs[index].content
    files_changed = files is not None and [str(f) for f in msgs[index].files] != files
    if (content_changed or files_changed) and msgs[index].role != "user":
        return flask.jsonify({"error": "Can only edit user messages"}), 400

    if content_changed or files_changed:
        replacements: dict = {}
        if content_changed:
            replacements["content"] = content
        if files_changed and files is not None:
            validated_files = _validate_message_file_references(
                files, manager.workspace
            )
            if isinstance(validated_files, tuple):
                return validated_files
            replacements["files"] = validated_files
        edited_msg = replace(msgs[index], **replacements)
        new_msgs = msgs[:index] + [edited_msg] + ([] if truncate else msgs[index + 1 :])
    elif truncate:
        new_msgs = msgs[: index + 1]
    else:
        return flask.jsonify({"error": "No changes requested"}), 400

    manager.edit(Log(new_msgs))

    # Build response with updated conversation
    log_dict = manager.to_dict(branches=True)

    # Emit SSE event
    SessionManager.add_event(
        conversation_id,
        {
            "type": "conversation_edited",
            "index": index,
            "truncated": truncate,
            "log": log_dict.get("log", []),
            "branches": log_dict.get("branches", {}),
        },
    )

    _invalidate_conversations_cache()
    return flask.jsonify(log_dict)


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/messages/<int:index>",
    methods=["DELETE"],
)
@require_auth
@api_doc(
    summary="Delete message from conversation (V2)",
    description="Delete a message from a conversation. Creates a backup branch before deleting.",
    responses={
        200: ConversationResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
    parameters=[
        CONVERSATION_ID_PARAM,
        {
            "name": "index",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
            "description": "Message index",
        },
    ],
    tags=["conversations-v2"],
)
def api_conversation_delete_message(conversation_id: str, index: int):
    """Delete a message from a conversation.

    Removes the message at the given index and keeps all other messages intact.
    Creates a backup branch before deleting.
    """
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    # Check if generation is in progress
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify(
                    {"error": "Cannot delete while generation is in progress"}
                ),
                409,
            )

    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    msgs = list(manager.log.messages)
    if index < 0 or index >= len(msgs):
        return (
            flask.jsonify(
                {"error": f"Message index {index} out of range (0-{len(msgs) - 1})"}
            ),
            404,
        )

    # Cannot delete system messages
    if msgs[index].role == "system":
        return flask.jsonify({"error": "Cannot delete system messages"}), 400

    new_msgs = msgs[:index] + msgs[index + 1 :]

    # Validate role sequence: consecutive same-role messages break LLM APIs.
    # Deleting a (non-system) message can only introduce a NEW adjacency at the
    # seam — between its nearest non-system neighbours on either side. Check only
    # that seam; scanning the whole conversation would reject valid deletions
    # whenever an unrelated pre-existing adjacency exists elsewhere in the log.
    prev_role = next(
        (m.role for m in reversed(msgs[:index]) if m.role != "system"), None
    )
    next_role = next((m.role for m in msgs[index + 1 :] if m.role != "system"), None)
    if prev_role is not None and prev_role == next_role:
        return (
            flask.jsonify(
                {
                    "error": f"Deleting this message would create consecutive {next_role!r} messages, which is not supported by LLM APIs"
                }
            ),
            400,
        )

    manager.edit(Log(new_msgs))

    # Build response with updated conversation
    log_dict = manager.to_dict(branches=True)

    # Emit SSE event (reuse conversation_edited — client handles log replacement)
    SessionManager.add_event(
        conversation_id,
        {
            "type": "conversation_edited",
            "index": index,
            "truncated": False,
            "log": log_dict.get("log", []),
            "branches": log_dict.get("branches", {}),
        },
    )

    _invalidate_conversations_cache()
    return flask.jsonify(log_dict)


@v2_api.route("/api/v2/conversations/<string:conversation_id>/fork", methods=["POST"])
@require_auth
@api_doc_simple(
    responses={
        200: SessionResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
    },
    tags=["conversations-v2"],
)
def api_conversation_fork(conversation_id: str):
    """Create a forked copy of a conversation from a specific message index."""
    if error := _validate_conversation_id(conversation_id):
        return error

    after_message_raw = request.args.get("after_message")
    if after_message_raw is None:
        return flask.jsonify(
            {"error": "after_message query parameter is required"}
        ), 400
    try:
        after_message = int(after_message_raw)
    except ValueError:
        return flask.jsonify({"error": "after_message must be an integer"}), 400

    branch = request.args.get("branch", "main")
    if error := _validate_branch(branch):
        return error

    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify({"error": "Cannot fork while generation is in progress"}),
                409,
            )

    try:
        manager = LogManager.load(conversation_id, branch=branch, lock=False)
    except FileNotFoundError:
        if branch == "main":
            return (
                flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
                404,
            )
        return (
            flask.jsonify({"error": f"Branch not found: {branch}"}),
            404,
        )

    source_messages = list(manager.log.messages)
    if after_message < 0 or after_message >= len(source_messages):
        return (
            flask.jsonify(
                {
                    "error": f"Message index {after_message} out of range (0-{len(source_messages) - 1})"
                }
            ),
            400,
        )

    new_conversation_id = _generate_fork_conversation_id()
    new_logdir = get_logs_dir() / new_conversation_id

    forked_messages = _copy_messages_for_fork(
        source_messages[: after_message + 1], manager.logdir, new_logdir
    )
    fork_manager = LogManager.load(
        logdir=new_logdir, initial_msgs=forked_messages, create=True, lock=False
    )
    fork_manager.write()

    source_config = ChatConfig.from_logdir(manager.logdir)
    fork_name = f"Fork of {manager.name} @ msg {after_message + 1}"
    replace(source_config, _logdir=new_logdir, name=fork_name).save()

    session = SessionManager.create_session(new_conversation_id)
    _invalidate_conversations_cache()
    return flask.jsonify(
        {
            "status": "ok",
            "conversation_id": new_conversation_id,
            "session_id": session.id,
        }
    )


@v2_api.route("/api/v2/conversations/<string:conversation_id>", methods=["DELETE"])
@require_auth
@api_doc(
    summary="Delete conversation (V2)",
    description="Delete a conversation and all its data using the V2 API",
    responses={
        200: StatusResponse,
        400: ErrorResponse,
        404: ErrorResponse,
        500: ErrorResponse,
    },
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_delete(conversation_id: str):
    """Delete a conversation."""

    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    # Defense-in-depth: verify path is within logs directory
    # (cannot use assert as it can be disabled with python -O)
    if logdir.parent != get_logs_dir():
        logger.warning(f"Path traversal attempt blocked: {conversation_id}")
        return flask.jsonify({"error": "Invalid conversation_id"}), 400
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    try:
        shutil.rmtree(logdir)
    except OSError as e:
        logger.error(f"Error deleting conversation {conversation_id}: {e}")
        return flask.jsonify({"error": f"Could not delete conversation: {e}"}), 500

    SessionManager.remove_all_sessions_for_conversation(conversation_id)

    _invalidate_conversations_cache()
    return flask.jsonify({"status": "ok"})


@v2_api.route("/api/v2/models")
@require_auth
@api_doc_simple(
    responses={200: StatusResponse, 500: ErrorResponse},
    tags=["models"],
)
def api_models():
    """Get available models.

    Returns available models based on current configuration.
    If proxy is configured, only returns proxy-supported models.
    """

    config = Config()

    # Check if proxy is configured
    proxy_url = config.get_env("LLM_PROXY_URL")
    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    is_proxy = bool(proxy_url and proxy_key)

    # Get default model
    default_model = get_default_model()

    # Get available models
    models_data: list[dict] = []

    # If proxy is configured (like gptme.ai), show supported providers
    # The proxy now supports both OpenAI and Anthropic models
    providers_to_check: list[Provider]
    if is_proxy:
        providers_to_check = cast(list[Provider], ["openai", "anthropic", "openrouter"])
    else:
        providers_to_check = cast(list[Provider], PROVIDERS)
    for provider in providers_to_check:
        provider_models = _get_models_for_provider(provider, dynamic_fetch=True)
        models = _apply_model_filters(provider_models, include_deprecated=False)
        models_data.extend(
            {
                "id": model.full,
                "provider": model.provider,
                "model": model.model,
                "context": model.context,
                "max_output": model.max_output,
                "supports_streaming": model.supports_streaming,
                "supports_vision": model.supports_vision,
                "supports_reasoning": model.supports_reasoning,
                "price_input": model.price_input,
                "price_output": model.price_output,
                "deprecated": model.deprecated,
            }
            for model in models
        )

    # Build recommended models list from core definitions
    recommended: list[str] = []
    for provider in providers_to_check:
        try:
            rec = get_recommended_model(provider)
            full_id = f"{provider}/{rec}"
            if any(m["id"] == full_id for m in models_data):
                recommended.append(full_id)
        except ValueError:
            pass

    # User-curated favorites (only those still available in the model list)
    available_ids = {m["id"] for m in models_data}
    favorites = [m for m in config.user.models.favorites if m in available_ids]

    return flask.jsonify(
        {
            "models": models_data,
            "default": default_model.full if default_model else None,
            "recommended": recommended,
            "favorites": favorites,
        }
    )


# Cache for provider health results
_provider_health_cache: dict[str, object] = {}
_provider_health_cache_time: float = 0.0
_provider_health_condition = threading.Condition()
_provider_health_refreshing = False
_PROVIDER_HEALTH_TTL = 60.0  # seconds
_PROVIDER_HEALTH_TIMEOUT = 5.0  # seconds per provider


def _probe_provider(provider_name: str) -> dict:
    """Probe a single provider and return {status, latency_ms, error}."""
    start = time.monotonic()

    def elapsed_ms() -> int:
        return round((time.monotonic() - start) * 1000)

    try:
        provider = cast(Provider, provider_name)

        if provider_name in ("openrouter", "local", "gptme") or is_custom_provider(
            provider
        ):
            # These support dynamic model listing via an HTTP call
            from ..llm import get_available_models  # fmt: skip

            models = get_available_models(provider)
            if not models:
                raise RuntimeError(f"No models returned from {provider_name}")

        elif provider_name == "anthropic":
            from ..llm.llm_anthropic import (
                get_client as get_anthropic_client,  # fmt: skip
            )
            from ..llm.llm_anthropic import init as init_anthropic  # fmt: skip

            anthropic_client = get_anthropic_client()
            if anthropic_client is None:
                init_anthropic(get_config())
                anthropic_client = get_anthropic_client()
            if anthropic_client is None:
                return {
                    "status": "error",
                    "latency_ms": elapsed_ms(),
                    "error": "Client not initialized",
                }
            # Lightweight list call to verify connectivity and auth
            anthropic_health_client = anthropic_client.with_options(
                timeout=_PROVIDER_HEALTH_TIMEOUT
            )
            next(iter(anthropic_health_client.models.list(limit=1)), None)

        elif provider_name in (
            "openai",
            "azure",
            "gemini",
            "xai",
            "groq",
            "deepseek",
            "nvidia",
            "moonshot",
        ):
            from ..llm.llm_openai import get_client as get_openai_client  # fmt: skip
            from ..llm.llm_openai import has_client  # fmt: skip
            from ..llm.llm_openai import init as init_openai  # fmt: skip

            if not has_client(provider):
                init_openai(provider, get_config())
            openai_client = get_openai_client(provider)
            openai_health_client = openai_client.with_options(
                timeout=_PROVIDER_HEALTH_TIMEOUT
            )
            next(iter(openai_health_client.models.list()), None)

        elif provider_name == "openai-subscription":
            from ..llm.llm_openai_subscription import (
                get_auth as get_subscription_auth,
            )

            get_subscription_auth(timeout=_PROVIDER_HEALTH_TIMEOUT)

        else:
            # Plugin or unknown provider — key is configured but no live check
            return {"status": "configured", "latency_ms": elapsed_ms(), "error": None}

        return {"status": "ok", "latency_ms": elapsed_ms(), "error": None}

    except Exception as e:
        return {"status": "error", "latency_ms": elapsed_ms(), "error": str(e)}


def _timeout_health_result() -> dict[str, object]:
    return {
        "status": "error",
        "latency_ms": round(_PROVIDER_HEALTH_TIMEOUT * 1000),
        "error": "Timeout",
    }


def _collect_provider_health(provider_names: list[str]) -> dict[str, dict]:
    if not provider_names:
        return {}

    executor = ThreadPoolExecutor(max_workers=len(provider_names))
    future_to_name = {
        executor.submit(_probe_provider, name): name for name in provider_names
    }

    try:
        done, not_done = futures_wait(future_to_name, timeout=_PROVIDER_HEALTH_TIMEOUT)
        results: dict[str, dict] = {}

        for future in done:
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = {
                    "status": "error",
                    "latency_ms": None,
                    "error": str(e),
                }

        for future in not_done:
            name = future_to_name[future]
            future.cancel()
            results[name] = _timeout_health_result()

        return results
    finally:
        # Avoid blocking the request thread on providers that ignore client-side timeouts.
        executor.shutdown(wait=False, cancel_futures=True)


@v2_api.route("/api/v2/providers/health")
@require_auth
@api_doc_simple(tags=["models"])
def api_providers_health():
    """Check health of all configured providers.

    Probes each configured provider with a lightweight API call (model listing)
    and returns status, latency, and any error message. Results are cached for
    60 seconds. Pass ``?force=1`` to bypass the cache.
    """
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    return flask.jsonify(_get_provider_health_response(force))


def _get_provider_health_response(force: bool) -> dict[str, object]:
    global _provider_health_cache, _provider_health_cache_time
    global _provider_health_refreshing

    with _provider_health_condition:
        while True:
            now = time.monotonic()
            cache_fresh = (now - _provider_health_cache_time) < _PROVIDER_HEALTH_TTL
            if not force and cache_fresh:
                return _provider_health_cache
            if not _provider_health_refreshing:
                _provider_health_refreshing = True
                break
            _provider_health_condition.wait()
            force = False

    try:
        available = list_available_providers()
        provider_names = [str(p) for p, _ in available]
        response: dict[str, object] = {
            "providers": _collect_provider_health(provider_names)
        }
    finally:
        with _provider_health_condition:
            if "response" in locals():
                _provider_health_cache = response
                _provider_health_cache_time = time.monotonic()
            _provider_health_refreshing = False
            _provider_health_condition.notify_all()

    return response


@v2_api.route("/api/v2/commands")
@require_auth
@api_doc_simple(
    responses={200: StatusResponse},
    tags=["commands"],
)
def api_commands():
    """Get available slash commands.

    Returns the list of registered commands that can be used via /command syntax.
    """
    from ..commands import get_user_commands

    return flask.jsonify({"commands": get_user_commands()})


@v2_api.route("/api/v2/conversations/<string:conversation_id>/config", methods=["GET"])
@require_auth
def api_conversation_config(conversation_id: str):
    """Get the chat config for a conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    chat_config_path = logdir / "config.toml"
    if chat_config_path.exists():
        chat_config = ChatConfig.from_logdir(logdir)
        return flask.jsonify(chat_config.to_dict())
    return (
        flask.jsonify({"error": f"Chat config not found: {conversation_id}"}),
        404,
    )


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/config", methods=["PATCH"]
)
@require_auth
def api_conversation_config_patch(conversation_id: str):
    """Update the chat config for a conversation."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    # Extract and type-check fields early (no side effects).
    # init_tools validation is deferred to after the 404/409 guards because it
    # mutates process-wide state (loaded_tools, _tools_init_lock).
    tool_allowlist: list[str] | None = None
    chat_patch = req_json.get("chat")
    if isinstance(chat_patch, dict):
        if "tools" in chat_patch:
            raw_allowlist = _get_optional_string_list_field(chat_patch, "tools")
            if isinstance(raw_allowlist, tuple):
                return raw_allowlist  # 400: bad type, no side effects
            tool_allowlist = raw_allowlist  # narrowed to list[str] | None
        if "model" in chat_patch and not isinstance(
            chat_patch["model"], str | type(None)
        ):
            return flask.jsonify(
                {
                    "error": "model must be a string (e.g. 'gpt-4', 'claude-sonnet-4-5-20250929')"
                }
            ), 400

    logdir = get_logs_dir() / conversation_id

    # Guard: check conversation exists before any side-effecting operations.
    # ChatConfig.save() creates the logdir on disk and set_config/init_tools mutate
    # process-wide state, so the 404 check must come first.
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    # Load conversation log BEFORE any side effects (config write, global state).
    # A directory can exist without a valid log file (partial deletion, corruption),
    # so we must verify the log is loadable before committing changes.
    try:
        manager = LogManager.load(conversation_id, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    # Reject config changes while generation is in progress — config PATCH rewrites
    # system messages and mutates global state, which races with streaming.
    sessions = SessionManager.get_sessions_for_conversation(conversation_id)
    for sess in sessions:
        if sess.generating:
            return (
                flask.jsonify(
                    {"error": "Cannot update config while generation is in progress"}
                ),
                409,
            )

    # Validate tool allowlist now that 404/409 guards have passed.
    # init_tools mutates process-wide state; must run AFTER guards, not before.
    if tool_allowlist is not None:
        try:
            init_tools(tool_allowlist)
        except ValueError as exc:
            return flask.jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return flask.jsonify({"error": f"Failed to load tool: {exc}"}), 400

    # Determine if workspace was explicitly provided in the request body.
    # This check must happen BEFORE ChatConfig.from_dict, which mutates the
    # chat_patch dict by inferring workspace from the existing logdir/workspace
    # path when workspace is absent (adding it as a side effect via the
    # `elif _logdir and (_logdir / "workspace").exists()` branch).
    workspace_in_patch = isinstance(chat_patch, dict) and "workspace" in chat_patch

    # Create and set config
    req_json["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    try:
        request_config = ChatConfig.from_dict(req_json)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    # Enforce workspace containment: server clients must not direct the agent
    # outside the conversation's logdir (path traversal / SSRF via workspace).
    # Only enforce when workspace was explicitly provided — when absent, from_dict
    # infers it from the existing logdir/workspace symlink (which may legitimately
    # point outside logdir for CLI-created conversations).
    if workspace_in_patch:
        if not request_config.workspace.is_relative_to(logdir.resolve()):
            return flask.jsonify(
                {"error": "workspace escapes conversation logdir"}
            ), 400

    try:
        chat_config = ChatConfig.load_or_create(logdir, request_config).save()
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400
    config = Config.from_workspace(workspace=chat_config.workspace)
    config.chat = chat_config
    set_config(config)

    # Initialize tools in this thread
    init_tools(chat_config.tools)

    tools = get_tools()

    if len(manager.log.messages) >= 1 and manager.log.messages[0].role == "system":
        # Remove leading system messages and replace with new ones
        # Use immutable Log interface instead of mutating the frozen dataclass's list
        first_non_system = 0
        for m in manager.log.messages:
            if m.role != "system":
                break
            first_non_system += 1
        remaining_msgs = list(manager.log.messages[first_non_system:])

        new_system_msgs = list(
            get_prompt(
                tools=tools,
                tool_format=chat_config.tool_format or "markdown",
                interactive=chat_config.interactive,
                model=chat_config.model,
                workspace=chat_config.workspace,
                agent_path=chat_config.agent,
            )
        )
        _append_conversation_system_prompt(
            new_system_msgs, _resolve_conversation_system_prompt(chat_config)
        )
        manager.log = Log(new_system_msgs + remaining_msgs)
    manager.write()

    _invalidate_conversations_cache()
    return flask.jsonify(
        {
            "status": "ok",
            "message": "Chat config updated",
            "config": chat_config.to_dict(),
            "tools": [t.name for t in tools],
        }
    )


@v2_api.route(
    "/api/v2/conversations/<string:conversation_id>/agent/avatar", methods=["GET"]
)
@require_auth
@api_doc(
    summary="Get agent avatar",
    description="Serve the agent's avatar image for a conversation",
    responses={200: None, 404: ErrorResponse},
    parameters=[
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Conversation ID",
        }
    ],
    tags=["conversations-v2"],
)
def api_conversation_agent_avatar(conversation_id: str):
    """Serve the agent's avatar image."""
    # Validate conversation_id to prevent path traversal
    if error := _validate_conversation_id(conversation_id):
        return error

    logdir = get_logs_dir() / conversation_id
    if not logdir.exists():
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    try:
        LogManager.load(logdir, lock=False)
    except FileNotFoundError:
        return (
            flask.jsonify({"error": f"Conversation not found: {conversation_id}"}),
            404,
        )

    chat_config = ChatConfig.load_or_create(logdir, ChatConfig())

    agent_config = chat_config.agent_config
    if not agent_config or not agent_config.avatar:
        return flask.jsonify({"error": "No avatar configured"}), 404

    avatar_path = agent_config.avatar

    # If it's a URL, redirect to it
    if avatar_path.startswith(("http://", "https://")):
        return flask.redirect(avatar_path)

    # Otherwise, serve the file from agent workspace
    if not chat_config.agent:
        return flask.jsonify({"error": "No agent path configured"}), 404

    full_path = chat_config.agent / avatar_path

    # Validate the path is within agent workspace (security) - do this BEFORE existence check
    try:
        full_path.resolve().relative_to(chat_config.agent.resolve())
    except ValueError:
        return flask.jsonify({"error": "Invalid avatar path"}), 400

    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


def _serve_agent_avatar(agent_path_str: str):
    """Shared helper to serve an agent's avatar by its workspace path."""
    from pathlib import Path

    agent_path = Path(agent_path_str)
    project_config = get_project_config(agent_path, quiet=True)
    if not project_config or not project_config.agent:
        return flask.jsonify({"error": "Agent not found"}), 404

    avatar = project_config.agent.avatar
    if not avatar:
        return flask.jsonify({"error": "No avatar configured"}), 404

    # If it's a URL, redirect to it
    if avatar.startswith(("http://", "https://")):
        return flask.redirect(avatar)

    # Otherwise, serve the file from agent workspace
    full_path = agent_path / avatar
    try:
        full_path.resolve().relative_to(agent_path.resolve())
    except ValueError:
        return flask.jsonify({"error": "Invalid avatar path"}), 400

    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


@v2_api.route("/api/v2/agents", methods=["GET"])
@require_auth
@api_doc(
    summary="List agents",
    description="List agents discovered from conversation history",
    responses={200: None},
    tags=["agents"],
)
def api_agents():
    """List agents extracted from conversations."""
    agent_map: dict[str, dict] = {}
    for conv in get_user_conversations(detail=False):
        if not conv.agent_path:
            continue
        path = conv.agent_path
        if path not in agent_map:
            agent_map[path] = {
                "name": conv.agent_name or path.split("/")[-1],
                "path": path,
                "has_avatar": conv.agent_avatar is not None,
                "urls": conv.agent_urls,
                "conversation_count": 0,
                "last_used": conv.modified,
            }
        entry = agent_map[path]
        entry["conversation_count"] += 1
        if conv.modified > entry["last_used"]:
            entry["last_used"] = conv.modified
            if conv.agent_urls:
                entry["urls"] = conv.agent_urls
            if conv.agent_avatar is not None:
                entry["has_avatar"] = True
    return flask.jsonify(list(agent_map.values()))


@v2_api.route("/api/v2/agents/avatar", methods=["GET"])
@require_auth
@api_doc(
    summary="Get agent avatar by path",
    description="Serve an agent's avatar image by its workspace path",
    responses={200: None, 404: ErrorResponse},
    parameters=[
        {
            "name": "path",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
            "description": "Agent workspace path",
        }
    ],
    tags=["agents"],
)
def api_agent_avatar():
    """Serve an agent's avatar by workspace path."""
    agent_path = request.args.get("path")
    if not agent_path:
        return flask.jsonify({"error": "path parameter required"}), 400
    return _serve_agent_avatar(agent_path)


@v2_api.route("/api/v2/user", methods=["GET"])
@require_auth
@api_doc(
    summary="Get user identity",
    description="Get user identity info (name, avatar) from global config",
    responses={200: None},
    tags=["user"],
)
def api_user():
    """Get user identity info."""
    user_config = load_user_config()
    return flask.jsonify(
        {
            "name": user_config.user.name,
            "avatar": user_config.user.avatar,
        }
    )


@v2_api.route("/api/v2/user/api-key", methods=["POST"])
@require_auth
@api_doc(
    summary="Save provider API key",
    description=(
        "Persist a provider API key into the user's global gptme config. "
        "Intended for first-run onboarding flows; callers should restart the "
        "server after a successful write if they need the running process to "
        "pick the key up immediately."
    ),
    request_body=UserApiKeySaveRequest,
    responses={200: UserApiKeySaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_api_key():
    """Persist a provider API key into user config."""
    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    provider = req_json.get("provider")
    api_key = req_json.get("api_key")
    model = req_json.get("model")
    if not isinstance(provider, str):
        return flask.jsonify({"error": "provider must be a string"}), 400
    if not isinstance(api_key, str):
        return flask.jsonify({"error": "api_key must be a string"}), 400
    if model is not None and not isinstance(model, str):
        return flask.jsonify(
            {
                "error": "model must be a string (e.g. 'gpt-4', 'claude-sonnet-4-5-20250929')"
            }
        ), 400
    if provider not in PROVIDER_API_KEYS:
        return flask.jsonify({"error": f"Unknown provider: {provider}"}), 400

    try:
        trimmed_api_key = _validate_api_key_input(api_key)
        trimmed_model = (
            _validate_model_input(model, expected_provider=provider)
            if model is not None
            else None
        )
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    env_var = PROVIDER_API_KEYS[provider]
    set_config_value(f"env.{env_var}", trimmed_api_key, reload=False, local=True)
    if trimmed_model is not None:
        set_config_value("env.MODEL", trimmed_model, reload=False, local=True)

    # Apply the new key immediately so the running server picks it up without restart.
    # os.environ takes priority over the config file in Config.get_env(), so the next
    # LLM call will use the new key.
    os.environ[env_var] = trimmed_api_key
    if trimmed_model is not None:
        os.environ["MODEL"] = trimmed_model

    logger.info(
        "Saved %s to user config via /api/v2/user/api-key (applied live)", env_var
    )
    return flask.jsonify(
        {
            "status": "ok",
            "provider": provider,
            "env_var": env_var,
            "restart_required": False,
        }
    )


@v2_api.route("/api/v2/user/default-model", methods=["POST"])
@require_auth
@api_doc(
    summary="Save default model",
    description=(
        "Persist the default model into the user's global gptme config and apply "
        "it to the running server when possible."
    ),
    request_body=UserDefaultModelSaveRequest,
    responses={200: UserDefaultModelSaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_default_model():
    """Persist the default model into user config."""
    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    model = req_json.get("model")
    if not isinstance(model, str):
        return flask.jsonify(
            {
                "error": "model must be a string (e.g. 'gpt-4', 'claude-sonnet-4-5-20250929')"
            }
        ), 400

    try:
        trimmed_model = _validate_model_input(model)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    restart_required = _persist_default_model(trimmed_model)
    logger.info("Saved MODEL=%s via /api/v2/user/default-model", trimmed_model)
    return flask.jsonify(
        {
            "status": "ok",
            "model": trimmed_model,
            "restart_required": restart_required,
        }
    )


@v2_api.route("/api/v2/user/favorites", methods=["POST"])
@require_auth
@api_doc(
    summary="Save favorite models",
    description="Persist the user's favorite models into the global gptme config.",
    request_body=UserFavoritesSaveRequest,
    responses={200: UserFavoritesSaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_favorites():
    """Persist favorite models into user config."""
    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    favorites = req_json.get("favorites")
    if not isinstance(favorites, list) or not all(
        isinstance(m, str) for m in favorites
    ):
        return flask.jsonify({"error": "favorites must be a list of strings"}), 400

    # Validate, de-duplicate (preserving order), and drop malformed entries so
    # junk doesn't accumulate in config.toml. Favorites may reference custom
    # providers, so we don't require a known built-in provider — just a
    # plausible "provider/model" shape within a sane length.
    seen: set[str] = set()
    cleaned: list[str] = []
    for m in favorites:
        trimmed = m.strip()
        if not trimmed or trimmed in seen:
            continue
        if "/" not in trimmed or len(trimmed) > 256:
            logger.warning("Dropping invalid favorite model id: %r", m)
            continue
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in trimmed):
            logger.warning("Dropping favorite model id with control chars: %r", m)
            continue
        seen.add(trimmed)
        cleaned.append(trimmed)

    set_config_value("models.favorites", cleaned, reload=True)
    logger.info("Saved %d favorite model(s) via /api/v2/user/favorites", len(cleaned))
    return flask.jsonify({"status": "ok", "favorites": cleaned})


@v2_api.route("/api/v2/user/avatar", methods=["GET"])
@require_auth
@api_doc(
    summary="Get user avatar",
    description="Serve the user's avatar image from global config",
    responses={200: None, 404: ErrorResponse},
    tags=["user"],
)
def api_user_avatar():
    """Serve the user's avatar image."""
    user_config = load_user_config()
    avatar_path = user_config.user.avatar
    if not avatar_path:
        return flask.jsonify({"error": "No avatar configured"}), 404

    # If it's a URL, redirect to it
    if avatar_path.startswith(("http://", "https://")):
        return flask.redirect(avatar_path)

    # Resolve the path (supports ~ expansion)
    from pathlib import Path

    full_path = Path(avatar_path).expanduser().resolve()

    # Security: validate path points to a raster image file to prevent serving
    # sensitive files (e.g. ~/.ssh/id_rsa) via a malicious config value.
    # SVG is excluded: it can embed <script> tags and execute JS in the
    # server's origin when navigated to directly (XSS via crafted SVG).
    if full_path.suffix.lower() not in _ALLOWED_AVATAR_EXTS:
        return flask.jsonify({"error": "Avatar must be an image file"}), 400

    if not full_path.exists():
        return flask.jsonify({"error": "Avatar file not found"}), 404

    if not _is_valid_image_content(full_path):
        return flask.jsonify({"error": "Avatar must be a valid image file"}), 400

    return flask.send_file(full_path)


@v2_api.route("/api/v2/user/config-file", methods=["GET"])
@require_auth
@api_doc(
    summary="Get raw user config file",
    description="Return the raw TOML contents of the user's main config.toml file.",
    responses={200: UserConfigFileResponse},
    tags=["user"],
)
def api_user_config_file_get():
    """Return raw config.toml contents for the settings UI."""
    content = _read_user_config_file_text()
    return flask.jsonify(_get_user_config_file_response(content))


@v2_api.route("/api/v2/user/config-file", methods=["PUT"])
@require_auth
@api_doc(
    summary="Replace raw user config file",
    description=(
        "Validate and replace the user's main config.toml file. "
        "Malformed TOML is rejected before anything is written."
    ),
    request_body=UserConfigFileSaveRequest,
    responses={200: UserConfigFileSaveResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_config_file_put():
    """Replace config.toml with validated TOML text."""
    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    content = req_json.get("content")
    if not isinstance(content, str):
        return flask.jsonify({"error": "content must be a string"}), 400

    try:
        tomlkit.loads(content)
    except Exception as exc:
        return flask.jsonify({"error": f"Invalid TOML: {exc}"}), 400

    config_file, _local_path = get_user_config_paths()
    config_file.parent.mkdir(parents=True, exist_ok=True)

    # If the submitted content contains redaction sentinels, restore the real
    # values from the on-disk file so the webui round-trip doesn't wipe secrets.
    original = config_file.read_text() if config_file.exists() else ""
    write_content = _restore_redacted_secrets(content, original)

    config_file.write_text(write_content)
    from gptme.config.core import reload_config

    reload_config()

    response = _get_user_config_file_response(write_content)
    response["status"] = "ok"
    return flask.jsonify(response)


@v2_api.route("/api/v2/user/config-file", methods=["PATCH"])
@require_auth
@api_doc(
    summary="Update one user config value",
    description=(
        "Update a single dotted key path in the user's main config.toml file. "
        "JSON scalar values preserve their TOML type on write: strings stay "
        "strings, booleans stay booleans, and numbers stay numbers."
    ),
    request_body=UserConfigFilePatchRequest,
    responses={200: UserConfigFilePatchResponse, 400: ErrorResponse},
    tags=["user"],
)
def api_user_config_file_patch():
    """Persist one dotted key path into config.toml."""
    req_json = request.get_json(silent=True)
    if req_json is None:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if not isinstance(req_json, dict):
        return flask.jsonify({"error": "JSON body must be an object"}), 400

    key = req_json.get("key")
    value = req_json.get("value")
    reload_config = req_json.get("reload", True)
    if not isinstance(key, str):
        return flask.jsonify({"error": "key must be a string"}), 400
    if not isinstance(value, str | int | float | bool):
        return flask.jsonify(
            {"error": "value must be a JSON scalar (string, number, or boolean)"}
        ), 400
    if not isinstance(reload_config, bool):
        return flask.jsonify({"error": "reload must be a boolean"}), 400

    try:
        key = _validate_config_key_path(key)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    try:
        set_config_value(key, value, reload=reload_config)
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400
    content = _read_user_config_file_text()
    response = _get_user_config_file_response(content)
    response["status"] = "ok"
    response["key"] = key
    return flask.jsonify(response)


@v2_api.route("/api/v2/audio/transcriptions", methods=["POST"])
@require_auth
@api_doc(
    summary="Transcribe uploaded audio with OpenRouter STT",
    description=(
        "Accept a short audio recording as multipart/form-data, forward it to "
        "OpenRouter's `/api/v1/audio/transcriptions` endpoint, and return the "
        "transcribed text."
    ),
    responses={
        200: AudioTranscriptionResponse,
        400: ErrorResponse,
        413: ErrorResponse,
        502: ErrorResponse,
        503: ErrorResponse,
    },
    tags=["audio"],
)
def api_audio_transcriptions():
    """Transcribe uploaded audio via OpenRouter."""
    audio_file = request.files.get("file")
    if audio_file is None or not audio_file.filename:
        return flask.jsonify({"error": "No audio file provided"}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return flask.jsonify({"error": "Audio file is empty"}), 400
    if len(audio_bytes) > _MAX_TRANSCRIPTION_AUDIO_BYTES:
        size_mb = len(audio_bytes) / 1024 / 1024
        return (
            flask.jsonify(
                {"error": (f"Audio file exceeds 25MB limit ({size_mb:.1f}MB)")}
            ),
            413,
        )

    requested_format = request.form.get("format")
    audio_format = (requested_format or "").strip().lower() or _guess_audio_format(
        audio_file.filename, audio_file.mimetype
    )
    if audio_format not in _SUPPORTED_TRANSCRIPTION_FORMATS:
        supported_formats = ", ".join(sorted(_SUPPORTED_TRANSCRIPTION_FORMATS))
        return (
            flask.jsonify(
                {
                    "error": (
                        "Unsupported audio format. "
                        f"Supported formats: {supported_formats}"
                    )
                }
            ),
            400,
        )

    requested_model = request.form.get("model")
    model = (requested_model or "").strip() or _DEFAULT_OPENROUTER_STT_MODEL

    language = (request.form.get("language") or "").strip().lower() or None
    if language and len(language) > 8:
        return flask.jsonify({"error": "language must be a short ISO code"}), 400

    try:
        result = _transcribe_audio_with_openrouter(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
            model=model,
            language=language,
        )
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 503
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.warning("OpenRouter STT request failed: %s %s", exc.code, detail)
        return (
            flask.jsonify(
                {
                    "error": (
                        f"OpenRouter STT request failed ({exc.code}): "
                        f"{detail or exc.reason}"
                    )
                }
            ),
            502,
        )
    except urllib.error.URLError as exc:
        logger.warning("OpenRouter STT network failure: %s", exc)
        return flask.jsonify(
            {"error": f"OpenRouter STT request failed: {exc.reason}"}
        ), 502

    text = result.get("text")
    if not isinstance(text, str):
        return flask.jsonify({"error": "OpenRouter STT response missing text"}), 502

    usage = result.get("usage")
    return flask.jsonify(
        {
            "text": text,
            "model": model,
            "usage": usage if isinstance(usage, dict) else None,
        }
    )


@v2_api.route("/api/v2/user/settings", methods=["GET"])
@require_auth
@api_doc(
    summary="Get current user settings",
    description=(
        "Return a read-only snapshot of the current user settings: which providers "
        "have API keys or OAuth tokens configured, and the active default model. "
        "Useful for settings UI and onboarding flows that need to reflect server-side "
        "state without keeping a local copy."
    ),
    responses={200: UserSettingsResponse},
    tags=["user"],
)
def api_user_settings():
    """Return the current user settings state."""
    available = list_available_providers()
    providers = [str(provider) for provider, _ in available]
    provider_sources = {
        str(provider): {
            "auth_source": auth_source,
            "effective_source": (
                "oauth"
                if auth_source == "oauth"
                else get_user_config_env_source(auth_source)
            ),
        }
        for provider, auth_source in available
    }
    default_model = get_default_model()
    return flask.jsonify(
        {
            "providers_configured": providers,
            "provider_sources": provider_sources,
            "default_model": default_model.full if default_model else None,
            "default_model_source": get_default_model_source(),
            "config_files": get_user_config_runtime_info(),
        }
    )


# In-memory conversations list cache.
# Caches the full list (no search, no detail) for _CONVERSATIONS_CACHE_TTL seconds.
# Invalidated on conversation create/update/delete via _invalidate_conversations_cache().
_CONVERSATIONS_CACHE_TTL = (
    30.0  # seconds — covers page navigation refresh within a session
)
_conversations_cache: list[ConversationMeta] | None = None
_conversations_cache_logs_dir: Path | None = None
_conversations_cache_time: float = 0.0


def _invalidate_conversations_cache() -> None:
    """Invalidate the conversations list cache."""
    global \
        _conversations_cache, \
        _conversations_cache_logs_dir, \
        _conversations_cache_time
    _conversations_cache = None
    _conversations_cache_logs_dir = None
    _conversations_cache_time = 0.0


def _update_conversation_in_cache(conv_id: str) -> None:
    """Refresh one conversation in the list cache without a full rescan.

    When a message is appended to an existing conversation, we only need to
    update that conversation's entry (last_message_preview, messages count,
    modified time). This avoids the O(N_conversations) filesystem scan that a
    full cache invalidation would trigger on the next conversations-list GET.

    Falls back to full invalidation if:
    - The cache is already stale (TTL expired) — next GET will rebuild anyway.
    - The conversation no longer exists on disk (e.g., concurrent deletion).
    """
    global _conversations_cache
    # Snapshot once to avoid TOCTOU: a concurrent _invalidate_conversations_cache()
    # can set the global to None between our is-None guard and the any()/comprehension
    # reads, which would raise TypeError on iteration.
    cache = _conversations_cache
    if cache is None:
        return
    if (time.monotonic() - _conversations_cache_time) >= _CONVERSATIONS_CACHE_TTL:
        return  # already stale; next GET will trigger a full rebuild
    logs_dir = _conversations_cache_logs_dir
    updated = get_conversation_meta_direct(conv_id, detail=False, logs_dir=logs_dir)
    if updated is None:
        # Conversation not found — fall back to full invalidation so the
        # stale entry is removed on the next list request.
        _invalidate_conversations_cache()
        return
    if not any(c.id == conv_id for c in cache):
        # Entry not in cached list (e.g., concurrent external filesystem write).
        # Fall back to full invalidation so the list gets rebuilt correctly.
        _invalidate_conversations_cache()
        return
    _conversations_cache = [updated if c.id == conv_id else c for c in cache]
