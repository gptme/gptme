"""Pure policy helpers for completion-time test verification."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .tools.base import ToolUse

if TYPE_CHECKING:
    from collections.abc import Sequence

_DOCUMENTATION_SUFFIXES = frozenset({".md", ".mdx", ".rst"})
_FIRST_CLASS_WRITERS = frozenset({"save", "append", "patch", "morph"})
_PATH_KEYS = ("path", "file", "paths", "files")
_SHELL_FAMILY = frozenset({"shell", "tmux", "ipython"})

# A command that only validates/builds the workspace is not evidence that the
# assistant authored source. Match only at command boundaries so an opaque script
# such as ``python generate.py`` stays conservative and arms verification.
_NON_AUTHORING_COMMAND = re.compile(
    r"^\s*(?:"
    r"(?:uv\s+run\s+)?pytest\b|tox\b|npm\s+(?:test|run\s+(?:test|build|lint|typecheck))\b|"
    r"cargo\s+(?:test|build|check|clippy)\b|make\s+(?:test|build|check|lint|typecheck)\b|"
    r"ruff\b|mypy\b|pyright\b|pre-commit\b|prek\b"
    r")(?:\s|$)"
)
_READ_ONLY_COMMAND = re.compile(
    r"^\s*(?:ls\b|pwd\b|head\b|tail\b|rg\b|grep\b|find\b|"
    r"cat\b(?![^\n;&|]*(?:>>?|1>|2>|&>))|"
    r"git\s+(?:status|log|diff|show|grep|rev-parse|branch)\b)"
)
_SHELL_WRITE_SIGNAL = re.compile(
    r"(?:^|\s)(?:>>?|1>|2>|&>)\s*\S|"
    r"\b(?:tee|touch|mkdir|rmdir|rm|mv|cp|ln|chmod|chown)\b|"
    r"\b(?:sed\s+[^|]*-i|perl\s+[^|]*-p?i)\b|"
    r"\bgit\s+(?:apply|restore|checkout|clean|reset|pull|merge|rebase|stash\s+pop)\b|"
    r"\b(?:write_text|write_bytes|open\s*\([^)]*['\"](?:w|a|x))\b|"
    r"\b(?:python|python3|bash|sh|node|deno)\s+\S+"
)
_MAKE_TEST_TARGET = re.compile(r"(?m)^test\s*(?::|::)")


@dataclass(frozen=True)
class VerificationCommand:
    """A repository-controlled command inferred from conventional manifests."""

    argv: tuple[str, ...]
    display: str
    reason: str
    source_fingerprints: tuple[tuple[Path, str], ...]
    trust: Literal["repository_controlled"] = "repository_controlled"
    preview: str | None = None


def _payload(tool_use: ToolUse) -> str:
    if tool_use.content:
        return tool_use.content
    kwargs = tool_use.kwargs or {}
    key = "code" if tool_use.tool == "ipython" else "command"
    value = kwargs.get(key)
    return value if isinstance(value, str) else ""


def _target_paths(tool_use: ToolUse) -> tuple[Path, ...]:
    values: list[str] = []
    if tool_use.kwargs:
        for key in _PATH_KEYS:
            value = tool_use.kwargs.get(key)
            if isinstance(value, str):
                values.extend(value.replace(",", "\n").splitlines())
    if not values and tool_use.args:
        values.append(tool_use.args[0])
    return tuple(Path(value.strip()) for value in values if value.strip())


def _patch_paths(tool_use: ToolUse) -> tuple[Path, ...]:
    payload = _payload(tool_use)
    markers = re.findall(r"^=== PATH:\s*(.+?)\s*===\s*$", payload, re.MULTILINE)
    return tuple(Path(marker) for marker in markers)


def classify_authoring_tool_use(tool_use: ToolUse) -> bool:
    """Return whether a tool call conservatively indicates authored workspace state."""
    if tool_use.tool in _FIRST_CLASS_WRITERS:
        paths = _target_paths(tool_use)
        if not paths and tool_use.tool == "patch":
            paths = _patch_paths(tool_use)
        return not paths or any(
            path.suffix.lower() not in _DOCUMENTATION_SUFFIXES for path in paths
        )
    if tool_use.tool not in _SHELL_FAMILY:
        return False

    payload = _payload(tool_use).strip()
    if (
        not payload
        or _NON_AUTHORING_COMMAND.match(payload)
        or _READ_ONLY_COMMAND.match(payload)
    ):
        return False
    if tool_use.tool == "tmux" and "send-keys" in payload:
        quoted = re.search(r"send-keys\s+(['\"])(.*?)\1", payload)
        payload = quoted.group(2) if quoted else payload
        if _NON_AUTHORING_COMMAND.match(payload) or _READ_ONLY_COMMAND.match(payload):
            return False
    return bool(_SHELL_WRITE_SIGNAL.search(payload))


def episode_has_authoring_mutation(messages: Sequence[object]) -> bool:
    """Scan the current user episode for a code-authoring tool call."""
    last_real_user = 0
    for index, message in enumerate(messages):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "") or ""
        if role == "user" and "No tool call detected in last message" not in content:
            last_real_user = index
    for message in messages[last_real_user + 1 :]:
        if getattr(message, "role", None) != "assistant":
            continue
        content = getattr(message, "content", "") or ""
        if any(
            classify_authoring_tool_use(use)
            for use in ToolUse.iter_from_content(content)
        ):
            return True
    return False


def _fingerprint(path: Path) -> tuple[tuple[Path, str], ...]:
    return ((path, hashlib.sha256(path.read_bytes()).hexdigest()),)


def fingerprints_match(command: VerificationCommand) -> bool:
    """Return whether manifests still match the command approved by the operator."""
    for path, expected in command.source_fingerprints:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _command(
    argv: tuple[str, ...], reason: str, source: Path, preview: str | None = None
) -> VerificationCommand:
    return VerificationCommand(
        argv=argv,
        display=shlex.join(argv),
        reason=reason,
        source_fingerprints=_fingerprint(source),
        preview=preview,
    )


def _pytest_config(workspace: Path) -> Path | None:
    for name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if name == "pytest.ini" or "[tool.pytest" in text or "[pytest]" in text:
            return path
    return None


def discover_verification_command(workspace: Path) -> VerificationCommand | None:
    """Discover one deterministic project-level test command."""
    pytest_config = _pytest_config(workspace)
    if pytest_config is not None:
        argv = (
            ("uv", "run", "pytest", "-x", "-q")
            if shutil.which("uv")
            else ("pytest", "-x", "-q")
        )
        return _command(
            argv,
            f"{pytest_config.name} contains explicit pytest configuration",
            pytest_config,
        )

    for name in ("tox.ini", "pyproject.toml", "setup.cfg"):
        path = workspace / name
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        if name == "tox.ini" or "[tool.tox" in text or "[tox]" in text:
            return _command(("tox",), f"{name} contains tox configuration", path)

    package = workspace / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text())
        except (OSError, json.JSONDecodeError):
            data = None
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
            lifecycle = [
                f"{name}: {scripts[name]}"
                for name in ("pretest", "test", "posttest")
                if isinstance(scripts.get(name), str)
            ]
            return _command(
                ("npm", "test"),
                "package.json defines an exact test script",
                package,
                preview="npm lifecycle scripts:\n" + "\n".join(lifecycle),
            )

    cargo = workspace / "Cargo.toml"
    if cargo.is_file():
        return _command(("cargo", "test"), "Cargo.toml defines a Cargo project", cargo)

    for name in ("Makefile", "makefile", "GNUmakefile"):
        makefile = workspace / name
        if makefile.is_file() and _MAKE_TEST_TARGET.search(
            makefile.read_text(errors="replace")
        ):
            return _command(("make", "test"), f"{name} defines a test target", makefile)
    return None
