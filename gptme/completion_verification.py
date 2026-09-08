"""Pure policy helpers for completion-time test verification."""

from __future__ import annotations

import configparser
import hashlib
import json
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .tools.base import ToolUse

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

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
# Split compound payloads so a leading test/read-only command cannot hide a later write.
_STATEMENT_SEP = re.compile(r"&&|\|\||[\n;]|[|](?![|])")


@dataclass(frozen=True)
class VerificationCommand:
    """A repository-controlled command inferred from conventional manifests."""

    argv: tuple[str, ...]
    display: str
    reason: str
    source_fingerprints: tuple[tuple[Path, str], ...]
    trust: Literal["repository_controlled"] = "repository_controlled"
    preview: str | None = None
    source_blobs: tuple[tuple[Path, bytes], ...] = ()


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
    if not payload:
        return False
    if tool_use.tool == "tmux" and "send-keys" in payload:
        quoted = re.search(r"send-keys\s+(['\"])(.*?)\1", payload)
        payload = quoted.group(2) if quoted else payload
    return _payload_has_authoring_statement(payload)


def _payload_has_authoring_statement(payload: str) -> bool:
    """Return whether any compound-command statement mutates workspace state."""
    for statement in _STATEMENT_SEP.split(payload):
        statement = statement.strip()
        if not statement:
            continue
        if _NON_AUTHORING_COMMAND.match(statement) or _READ_ONLY_COMMAND.match(
            statement
        ):
            continue
        if _SHELL_WRITE_SIGNAL.search(statement):
            return True
    return False


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
) -> VerificationCommand | None:
    try:
        data = source.read_bytes()
    except OSError:
        return None
    return VerificationCommand(
        argv=argv,
        display=shlex.join(argv),
        reason=reason,
        source_fingerprints=((source, hashlib.sha256(data).hexdigest()),),
        preview=preview,
        source_blobs=((source, data),),
    )


def _toml_has_table(text: str, *path: str) -> bool:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    current: object = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return True


def _ini_has_section(text: str, *names: str) -> bool:
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return False
    sections = {section.lower() for section in parser.sections()}
    wanted = {name.lower() for name in names}
    return bool(sections & wanted)


def _pytest_config(workspace: Path) -> Path | None:
    for name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if name == "pytest.ini":
            return path
        if name == "pyproject.toml" and _toml_has_table(text, "tool", "pytest"):
            return path
        if name == "setup.cfg" and _ini_has_section(text, "pytest", "tool:pytest"):
            return path
        if name == "tox.ini" and _ini_has_section(text, "pytest"):
            return path
    return None


def _has_tox_config(name: str, text: str) -> bool:
    if name == "tox.ini":
        return _ini_has_section(text, "tox")
    if name == "pyproject.toml":
        return _toml_has_table(text, "tool", "tox")
    return _ini_has_section(text, "tox", "tox:tox")


def uses_approved_snapshot(command: VerificationCommand) -> bool:
    """Return whether execution can bind to approved manifest bytes."""
    if not command.source_blobs:
        return False
    name = command.source_blobs[0][0].name
    return name in {"Makefile", "makefile", "GNUmakefile", "package.json", "pytest.ini"}


def npm_lifecycle_script(blob: bytes) -> str | None:
    """Rebuild npm's pretest/test/posttest sequence from approved package.json bytes."""
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict) or not isinstance(scripts.get("test"), str):
        return None
    lines = [
        "#!/bin/sh",
        "set -e",
        'export PATH="$PWD/node_modules/.bin:$PATH"',
    ]
    for name in ("pretest", "test", "posttest"):
        script = scripts.get(name)
        if isinstance(script, str):
            lines.append(script)
    return "\n".join(lines) + "\n"


def approved_execution_argv(
    command: VerificationCommand, snapshot_dir: Path, workspace: Path | None
) -> tuple[str, ...]:
    """Return argv that executes the approved snapshot rather than a live manifest."""
    if not command.source_blobs:
        return command.argv
    source, blob = command.source_blobs[0]
    name = source.name
    snap = snapshot_dir / name
    snap.write_bytes(blob)
    root = str(workspace) if workspace is not None else "."
    if name in {"Makefile", "makefile", "GNUmakefile"}:
        return ("make", "-f", str(snap), "test")
    if name == "package.json":
        wrapper = snapshot_dir / "npm-test.sh"
        body = npm_lifecycle_script(blob)
        if body is None:
            return command.argv
        wrapper.write_text(body)
        wrapper.chmod(0o700)
        return (str(wrapper),)
    if name == "pytest.ini":
        parts = list(command.argv)
        try:
            idx = parts.index("pytest")
        except ValueError:
            return command.argv
        return tuple(
            parts[: idx + 1] + ["-c", str(snap), "--rootdir", root] + parts[idx + 1 :]
        )
    return command.argv


def discover_verification_command(workspace: Path) -> VerificationCommand | None:
    """Discover one deterministic project-level test command."""
    pytest_config = _pytest_config(workspace)
    if pytest_config is not None:
        argv = (
            ("uv", "run", "pytest", "-x", "-q")
            if shutil.which("uv")
            else ("pytest", "-x", "-q")
        )
        command = _command(
            argv,
            f"{pytest_config.name} contains explicit pytest configuration",
            pytest_config,
        )
        if command is not None:
            return command

    for name in ("tox.ini", "pyproject.toml", "setup.cfg"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _has_tox_config(name, text):
            command = _command(("tox",), f"{name} contains tox configuration", path)
            if command is not None:
                return command

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
            command = _command(
                ("npm", "test"),
                "package.json defines an exact test script",
                package,
                preview="npm lifecycle scripts:\n" + "\n".join(lifecycle),
            )
            if command is not None:
                return command

    cargo = workspace / "Cargo.toml"
    if cargo.is_file():
        command = _command(
            ("cargo", "test"), "Cargo.toml defines a Cargo project", cargo
        )
        if command is not None:
            return command

    for name in ("Makefile", "makefile", "GNUmakefile"):
        makefile = workspace / name
        if not makefile.is_file():
            continue
        try:
            text = makefile.read_text(errors="replace")
        except OSError:
            continue
        if _MAKE_TEST_TARGET.search(text):
            command = _command(
                ("make", "test"), f"{name} defines a test target", makefile
            )
            if command is not None:
                return command
    return None
