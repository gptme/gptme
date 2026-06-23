"""Helpers for generating and verifying gptme output attestations."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .__version__ import __version__
from .llm.models.resolution import get_default_model
from .util.git_worktree import get_git_root

_SESSION_ENV_VARS = (
    "GPTME_SESSION_ID",
    "BOB_SESSION_ID",
    "SESSION_ID",
    "GIT_COMMITTER_SESSION_ID",
)
_MODEL_ENV_VARS = (
    "GPTME_MODEL",
    "CLAUDE_MODEL",
    "CC_MODEL",
)
_BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHA256_BASE62_LENGTH = 43


class AttestationError(ValueError):
    """Raised when attestation generation or verification fails."""


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _sha256_prefixed_hex(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _base62_encode(data: bytes, *, min_length: int = 1) -> str:
    number = int.from_bytes(data, "big")
    if number == 0:
        return _BASE62_ALPHABET[0] * max(min_length, 1)

    chars: list[str] = []
    while number:
        number, remainder = divmod(number, 62)
        chars.append(_BASE62_ALPHABET[remainder])
    return "".join(reversed(chars)).rjust(min_length, _BASE62_ALPHABET[0])


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise AttestationError(stderr)
    return result.stdout.strip()


def resolve_workspace_root(workspace: str | Path | None = None) -> Path:
    root = get_git_root(Path(workspace) if workspace is not None else None)
    if root is None:
        raise AttestationError("Current directory is not inside a git repository.")
    return root


def get_workspace_commit(workspace_root: Path) -> str:
    return _run_git(workspace_root, ["rev-parse", "HEAD"])


def get_agent_id() -> str:
    agent_name = os.environ.get("GPTME_AGENT_NAME")
    hostname = socket.gethostname()
    if agent_name:
        return f"{agent_name}@{hostname}"
    try:
        username = getpass.getuser()
    except (KeyError, OSError):
        getuid = getattr(os, "getuid", None)
        username = f"uid{getuid()}" if callable(getuid) else "unknown"
    return f"{username}@{hostname}"


def get_session_id() -> str:
    for key in _SESSION_ENV_VARS:
        value = os.environ.get(key)
        if value:
            return value
    return "unknown"


def get_model_identity() -> tuple[str, str]:
    for key in _MODEL_ENV_VARS:
        value = os.environ.get(key)
        if value:
            if "/" in value:
                provider, _ = value.split("/", 1)
                return value, provider
            return value, "unknown"

    model = get_default_model()
    if model is None:
        return "unknown", "unknown"
    return model.full, str(model.provider)


def _build_payload(
    *,
    workspace_commit: str,
    output_type: str,
    content_hash: str,
    output_path: str | None,
    url: str | None,
) -> dict[str, Any]:
    model_id, model_provider = get_model_identity()
    output: dict[str, Any] = {
        "type": output_type,
        "sha256": content_hash,
    }
    if output_path is not None:
        output["path"] = output_path
    if url is not None:
        output["url"] = url

    return {
        "gptme_id": "v1",
        "issued_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "agent": {
            "id": get_agent_id(),
            "workspace_commit": workspace_commit,
            "gptme_version": __version__,
            "session_id": get_session_id(),
        },
        "model": {
            "id": model_id,
            "provider": model_provider,
        },
        "output": output,
    }


def _attestation_id(payload: dict[str, Any]) -> str:
    digest = _sha256_bytes(_canonical_json(payload).encode("utf-8"))
    return f"gai_{_base62_encode(digest, min_length=_SHA256_BASE62_LENGTH)}"


def create_file_attestation(
    target: Path,
    *,
    workspace: str | Path | None = None,
    output_type: str = "file",
    url: str | None = None,
) -> tuple[dict[str, Any], Path]:
    workspace_root = resolve_workspace_root(workspace or target.parent).resolve()
    target = target.resolve()

    try:
        relative_target = target.relative_to(workspace_root)
    except ValueError as exc:
        raise AttestationError(
            f"Target {target} is outside workspace {workspace_root}."
        ) from exc

    content = target.read_bytes()
    payload = _build_payload(
        workspace_commit=get_workspace_commit(workspace_root),
        output_type=output_type,
        content_hash=_sha256_prefixed_hex(content),
        output_path=relative_target.as_posix(),
        url=url,
    )
    payload["id"] = _attestation_id(payload)
    return payload, workspace_root


def create_text_attestation(
    text: str,
    *,
    workspace: str | Path | None = None,
    output_type: str = "text",
    url: str | None = None,
) -> tuple[dict[str, Any], Path]:
    workspace_root = resolve_workspace_root(workspace)
    payload = _build_payload(
        workspace_commit=get_workspace_commit(workspace_root),
        output_type=output_type,
        content_hash=_sha256_prefixed_hex(text.encode("utf-8")),
        output_path=None,
        url=url,
    )
    payload["id"] = _attestation_id(payload)
    return payload, workspace_root


def default_attestation_path(workspace_root: Path, attestation_id: str) -> Path:
    return workspace_root / "state" / "attestations" / f"{attestation_id}.json"


def write_attestation(
    attestation: dict[str, Any],
    *,
    workspace_root: Path,
    destination: Path | None = None,
) -> Path:
    out_path = destination or default_attestation_path(
        workspace_root, attestation["id"]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n")
    return out_path


def load_attestation(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AttestationError(f"Invalid attestation JSON: {path}") from exc

    if not isinstance(data, dict):
        raise AttestationError("Attestation must be a JSON object.")
    return data


def verify_attestation_id(attestation: dict[str, Any]) -> None:
    actual_id = attestation.get("id")
    if not isinstance(actual_id, str) or not actual_id.startswith("gai_"):
        raise AttestationError("Attestation is missing a valid id.")

    payload = dict(attestation)
    payload.pop("id", None)
    expected_id = _attestation_id(payload)
    if actual_id != expected_id:
        raise AttestationError(
            f"Attestation id mismatch: expected {expected_id}, found {actual_id}."
        )


def verify_workspace_commit(workspace_root: Path, attestation: dict[str, Any]) -> None:
    try:
        commit = attestation["agent"]["workspace_commit"]
    except KeyError as exc:
        raise AttestationError("Attestation missing agent.workspace_commit.") from exc

    if not isinstance(commit, str) or not commit:
        raise AttestationError("Attestation has invalid agent.workspace_commit.")

    _run_git(workspace_root, ["cat-file", "-e", f"{commit}^{{commit}}"])


def _resolve_content_path(
    workspace_root: Path,
    attestation: dict[str, Any],
    content_path: Path | None,
) -> Path:
    if content_path is not None:
        return content_path.resolve()

    output = attestation.get("output")
    if not isinstance(output, dict):
        raise AttestationError("Attestation missing output metadata.")

    path_value = output.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise AttestationError(
            "Attestation does not embed an output path; pass --content-file to verify."
        )

    resolved_path = (workspace_root / path_value).resolve()
    workspace_root = workspace_root.resolve()
    try:
        resolved_path.relative_to(workspace_root)
    except ValueError as exc:
        raise AttestationError(
            f"Attested content path escapes workspace: {path_value}"
        ) from exc
    return resolved_path


def verify_content_hash(
    workspace_root: Path,
    attestation: dict[str, Any],
    *,
    content_path: Path | None = None,
    text: str | None = None,
) -> None:
    if content_path is not None and text is not None:
        raise AttestationError("Pass either content_path or text, not both.")

    output = attestation.get("output")
    if not isinstance(output, dict):
        raise AttestationError("Attestation missing output metadata.")

    expected_hash = output.get("sha256")
    if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:"):
        raise AttestationError("Attestation has invalid output.sha256.")

    if text is not None:
        actual_hash = _sha256_prefixed_hex(text.encode("utf-8"))
    else:
        resolved_path = _resolve_content_path(workspace_root, attestation, content_path)
        if not resolved_path.exists():
            raise AttestationError(f"Attested content not found: {resolved_path}")
        actual_hash = _sha256_prefixed_hex(resolved_path.read_bytes())

    if actual_hash != expected_hash:
        raise AttestationError(
            f"Content hash mismatch: expected {expected_hash}, found {actual_hash}."
        )


def verify_attestation(
    attestation_path: Path,
    *,
    workspace: str | Path | None = None,
    content_path: Path | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    attestation = load_attestation(attestation_path)
    if attestation.get("gptme_id") != "v1":
        raise AttestationError("Unsupported attestation version.")

    workspace_root = resolve_workspace_root(workspace)
    verify_attestation_id(attestation)
    verify_workspace_commit(workspace_root, attestation)
    verify_content_hash(
        workspace_root,
        attestation,
        content_path=content_path,
        text=text,
    )
    return attestation
