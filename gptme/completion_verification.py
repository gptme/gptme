"""Pure policy helpers for completion-time test verification."""

from __future__ import annotations

import configparser
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
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
_NPM_ENV_KEY = re.compile(r"[^A-Za-z0-9]+")


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


@dataclass
class ApprovedBind:
    """In-memory side effects of binding a run to approved manifest bytes.

    Cleanup must use this object, never files the child process can rewrite.
    """

    extra_cleanup: list[Path] = field(default_factory=list)
    swapped_live: Path | None = None
    swapped_backup: bytes | None = None
    swapped_missing: bool = False
    swapped_approved: bytes | None = None


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


def _split_shell_statements(payload: str) -> list[str]:
    """Split a payload on unquoted separators, keeping heredoc bodies intact."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(payload)
    quote: str | None = None
    heredoc_delim: str | None = None
    heredoc_pending: str | None = None

    def flush() -> None:
        statements.append("".join(buf))
        buf.clear()

    while i < n:
        ch = payload[i]
        if heredoc_delim is not None:
            line_end = payload.find("\n", i)
            line = payload[i:] if line_end == -1 else payload[i:line_end]
            buf.append(payload[i:] if line_end == -1 else payload[i : line_end + 1])
            if line.strip() == heredoc_delim:
                heredoc_delim = None
            if line_end == -1:
                break
            i = line_end + 1
            continue
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and quote != "'" and i + 1 < n:
                buf.append(payload[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "#" and (not buf or buf[-1] in " \t\n;&|"):
            while i < n and payload[i] != "\n":
                i += 1
            continue
        if payload.startswith("<<", i) and not payload.startswith("<<<", i):
            buf.append("<<")
            i += 2
            if i < n and payload[i] == "-":
                buf.append("-")
                i += 1
            while i < n and payload[i] in " \t":
                buf.append(payload[i])
                i += 1
            delim: list[str] = []
            if i < n and payload[i] in "'\"":
                q = payload[i]
                buf.append(q)
                i += 1
                while i < n and payload[i] != q:
                    delim.append(payload[i])
                    buf.append(payload[i])
                    i += 1
                if i < n:
                    buf.append(payload[i])
                    i += 1
            else:
                while i < n and payload[i] not in " \t\n;&|<>":
                    delim.append(payload[i])
                    buf.append(payload[i])
                    i += 1
            heredoc_pending = "".join(delim) or None
            continue
        if ch == "\n" and heredoc_pending is not None:
            buf.append(ch)
            heredoc_delim = heredoc_pending
            heredoc_pending = None
            i += 1
            continue
        if payload.startswith("&&", i) or payload.startswith("||", i):
            flush()
            i += 2
            continue
        if ch in ";\n" or (ch == "|" and (i + 1 >= n or payload[i + 1] != "|")):
            flush()
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return statements


def _mask_inert_shell_text(statement: str) -> str:
    """Blank quoted strings and heredoc bodies so write-signal matches stay real."""
    chars = list(statement)
    i = 0
    n = len(statement)
    quote: str | None = None
    while i < n:
        ch = statement[i]
        if quote is not None:
            chars[i] = " "
            if ch == "\\" and quote != "'" and i + 1 < n:
                chars[i + 1] = " "
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            chars[i] = " "
            quote = ch
            i += 1
            continue
        if statement.startswith("<<", i) and not statement.startswith("<<<", i):
            i += 2
            if i < n and statement[i] == "-":
                i += 1
            while i < n and statement[i] in " \t":
                i += 1
            delim: list[str] = []
            if i < n and statement[i] in "'\"":
                q = statement[i]
                i += 1
                while i < n and statement[i] != q:
                    delim.append(statement[i])
                    i += 1
                if i < n:
                    i += 1
            else:
                while i < n and statement[i] not in " \t\n;&|<>":
                    delim.append(statement[i])
                    i += 1
            newline = statement.find("\n", i)
            if newline == -1 or not delim:
                break
            i = newline + 1
            token = "".join(delim)
            while i < n:
                line_end = statement.find("\n", i)
                line = statement[i:] if line_end == -1 else statement[i:line_end]
                end = n if line_end == -1 else line_end
                if line.strip() == token:
                    i = n if line_end == -1 else line_end + 1
                    break
                for j in range(i, end):
                    chars[j] = " "
                if line_end == -1:
                    break
                i = line_end + 1
            continue
        i += 1
    return "".join(chars)


def _payload_has_authoring_statement(payload: str) -> bool:
    """Return whether any compound-command statement mutates workspace state."""
    for statement in _split_shell_statements(payload):
        statement = statement.strip()
        if not statement:
            continue
        active = _mask_inert_shell_text(statement)
        if _NON_AUTHORING_COMMAND.match(statement) or _READ_ONLY_COMMAND.match(
            statement
        ):
            continue
        if _SHELL_WRITE_SIGNAL.search(active):
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
    return bool(command.source_blobs)


def _sh_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _cmd_set(name: str, value: str) -> str:
    escaped = value.replace("%", "%%").replace('"', '""')
    return f'set "{name}={escaped}"'


def _flatten_npm_package_env(data: dict) -> dict[str, str]:
    env: dict[str, str] = {}

    def walk(prefix: str, value: object) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, inner in value.items():
                safe = _NPM_ENV_KEY.sub("_", str(key)).strip("_")
                if not safe:
                    continue
                walk(f"{prefix}_{safe}", inner)
        elif isinstance(value, list):
            for index, inner in enumerate(value):
                walk(f"{prefix}_{index}", inner)
        elif isinstance(value, bool):
            env[prefix] = "true" if value else "false"
        else:
            env[prefix] = str(value)

    walk("npm_package", data)
    return env


def _npm_lifecycle_events(blob: bytes) -> tuple[dict, list[tuple[str, str]]] | None:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or not isinstance(scripts.get("test"), str):
        return None
    events = [
        (name, scripts[name])
        for name in ("pretest", "test", "posttest")
        if isinstance(scripts.get(name), str)
    ]
    return data, events


def npm_lifecycle_script(blob: bytes) -> str | None:
    """Rebuild npm's pretest/test/posttest sequence from approved package.json bytes.

    Each lifecycle event runs in its own ``sh -c`` process with npm's
    ``INIT_CWD``, ``npm_lifecycle_event``, ``npm_lifecycle_script``, and
    flattened ``npm_package_*`` environment, matching ``npm test`` isolation.
    """
    parsed = _npm_lifecycle_events(blob)
    if parsed is None:
        return None
    data, events = parsed
    lines = [
        "set -e",
        'export PATH="$PWD/node_modules/.bin:$PATH"',
        'export INIT_CWD="${INIT_CWD:-$PWD}"',
    ]
    for key, value in _flatten_npm_package_env(data).items():
        lines.append(f"export {key}={_sh_single_quote(value)}")
    for event, script in events:
        lines.append(f"export npm_lifecycle_event={_sh_single_quote(event)}")
        lines.append(f"export npm_lifecycle_script={_sh_single_quote(script)}")
        lines.append(f"sh -c {_sh_single_quote(script)}")
    return "\n".join(lines) + "\n"


def _workspace_side_file(
    snapshot_dir: Path, workspace: Path | None, blob: bytes, suffix: str
) -> Path:
    """Write approved bytes under the workspace so toxinidir stays the project root."""
    archived = snapshot_dir / f"manifest{suffix}"
    archived.write_bytes(blob)
    root = workspace if workspace is not None else snapshot_dir
    fd, path = tempfile.mkstemp(prefix=".gptme-verify-", suffix=suffix, dir=root)
    os.close(fd)
    bound = Path(path)
    bound.write_bytes(blob)
    return bound


def _swap_workspace_manifest(
    workspace: Path | None, source: Path, blob: bytes
) -> tuple[Path, bytes | None, bool]:
    """Point a live-path runner at approved bytes for the duration of the run.

    Cargo requires the filename ``Cargo.toml`` and uses that file's parent as
    the package root, so a side-file snapshot cannot be used.
    """
    live = (workspace / source.name) if workspace is not None else source
    try:
        backup = live.read_bytes()
        missing = False
    except OSError:
        backup = None
        missing = True
    live.write_bytes(blob)
    return live, backup, missing


def restore_approved_snapshots(bind: ApprovedBind) -> None:
    """Undo workspace side-effects using in-memory bind metadata only."""
    live = bind.swapped_live
    if live is not None:
        try:
            current = live.read_bytes()
        except OSError:
            current = None
        if current is not None and current == bind.swapped_approved:
            if bind.swapped_missing:
                with contextlib.suppress(OSError):
                    live.unlink()
            elif bind.swapped_backup is not None:
                with contextlib.suppress(OSError):
                    live.write_bytes(bind.swapped_backup)
    for extra in bind.extra_cleanup:
        with contextlib.suppress(OSError):
            extra.unlink()


def _ini_section(name: str, opts: dict) -> bytes:
    lines = [f"[{name}]"]
    for key, value in opts.items():
        if isinstance(value, dict):
            continue
        if isinstance(value, list):
            rendered = "\n    ".join(str(item) for item in value)
            lines.append(f"{key} =\n    {rendered}")
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        else:
            lines.append(f"{key} = {value}")
    return ("\n".join(lines) + "\n").encode()


def _pytest_ini_from_blob(name: str, blob: bytes) -> bytes:
    if name == "pytest.ini":
        return blob
    text = blob.decode("utf-8", errors="replace")
    if name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return b"[pytest]\n"
        tool = data.get("tool") if isinstance(data, dict) else None
        pytest_tbl = tool.get("pytest") if isinstance(tool, dict) else None
        if not isinstance(pytest_tbl, dict):
            return b"[pytest]\n"
        opts = pytest_tbl.get("ini_options", pytest_tbl)
        if not isinstance(opts, dict):
            return b"[pytest]\n"
        return _ini_section("pytest", opts)
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return b"[pytest]\n"
    for section in ("pytest", "tool:pytest"):
        if parser.has_section(section):
            return _ini_section("pytest", dict(parser.items(section)))
    return b"[pytest]\n"


def _npm_snapshot_argv(snapshot_dir: Path, blob: bytes) -> tuple[str, ...] | None:
    parsed = _npm_lifecycle_events(blob)
    if parsed is None:
        return None
    data, events = parsed
    if os.name == "nt":
        wrapper = snapshot_dir / "npm-test.cmd"
        lines = [
            "@echo off",
            "setlocal EnableExtensions",
            'set "PATH=%CD%\\node_modules\\.bin;%PATH%"',
            'if not defined INIT_CWD set "INIT_CWD=%CD%"',
        ]
        for key, value in _flatten_npm_package_env(data).items():
            lines.append(_cmd_set(key, value))
        for event, script in events:
            fragment = snapshot_dir / f"npm-{event}.cmd"
            fragment.write_text(script + "\r\n", encoding="utf-8")
            lines.append(_cmd_set("npm_lifecycle_event", event))
            lines.append(_cmd_set("npm_lifecycle_script", script))
            lines.append(f'cmd /d /s /c "{fragment}"')
            lines.append("if errorlevel 1 exit /b 1")
        wrapper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return (comspec, "/d", "/s", "/c", str(wrapper))
    wrapper = snapshot_dir / "npm-test.sh"
    body = npm_lifecycle_script(blob)
    if body is None:
        return None
    wrapper.write_text(body, encoding="utf-8")
    wrapper.chmod(0o700)
    sh = shutil.which("sh") or "/bin/sh"
    return (sh, str(wrapper))


def approved_execution_argv(
    command: VerificationCommand, snapshot_dir: Path, workspace: Path | None
) -> tuple[tuple[str, ...], ApprovedBind]:
    """Return argv plus in-memory bind metadata for the approved snapshot."""
    bind = ApprovedBind()
    if not command.source_blobs:
        return command.argv, bind
    source, blob = command.source_blobs[0]
    name = source.name
    snap = snapshot_dir / name
    snap.write_bytes(blob)
    root = str(workspace) if workspace is not None else "."
    if name in {"Makefile", "makefile", "GNUmakefile"}:
        return ("make", "-f", str(snap), "test"), bind
    if name == "package.json":
        argv = _npm_snapshot_argv(snapshot_dir, blob)
        return (command.argv if argv is None else argv), bind
    if "pytest" in command.argv:
        ini_path = snapshot_dir / "pytest.ini"
        ini_path.write_bytes(_pytest_ini_from_blob(name, blob))
        parts = list(command.argv)
        try:
            idx = parts.index("pytest")
        except ValueError:
            return command.argv, bind
        return (
            tuple(
                parts[: idx + 1]
                + ["-c", str(ini_path), "--rootdir", root]
                + parts[idx + 1 :]
            ),
            bind,
        )
    if command.argv[:1] == ("tox",):
        suffix = Path(name).suffix or ".ini"
        bound = _workspace_side_file(snapshot_dir, workspace, blob, suffix)
        bind.extra_cleanup.append(bound)
        return ("tox", "-c", str(bound)), bind
    if name == "Cargo.toml":
        live, backup, missing = _swap_workspace_manifest(workspace, source, blob)
        bind.swapped_live = live
        bind.swapped_backup = backup
        bind.swapped_missing = missing
        bind.swapped_approved = blob
        return command.argv, bind
    return command.argv, bind


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
