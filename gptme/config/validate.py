"""Read-only config corruption detector.

Provides ``detect_config_problems()`` and ``fix_providers_corruption()`` for
 the ``--check-config`` / ``--check-config --fix`` CLI surface.

This module must be imported lazily (inside the option handler) so that
ordinary ``gptme --help`` / ``--version`` invocations stay within the
existing import budget.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomllib

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class ConfigProblem:
    name: str  # e.g. "providers-not-array"
    path: Path  # file containing the problem
    description: str  # human-readable description
    repair_cmd: str  # concrete command to repair


def _repaired_providers_text(text: str) -> str | None:
    """Recognize only the old saver's root-level array of string assignments.

    Parse every assignment and the entire candidate before returning it. Unknown
    syntax is a manual repair, never a reason to discard part of the input.
    """
    start = re.search(r"^providers[ \t]*=[ \t]*\[", text, re.MULTILINE)
    if start is None:
        return None
    # Consume each token once: an unclosed/comment-heavy array must not cause
    # exponential regex backtracking while searching for the closing bracket.
    token = re.compile(r'"(?:[^"\\\r\n]|\\.)*"|#[^\r\n]*|[^"\]#]+')
    end = start.end()
    while end < len(text) and text[end] != "]":
        part = token.match(text, end)
        if part is None:
            return None
        end = part.end()
    if end == len(text):
        return None
    match = re.match(r"\][ \t]*(?=#|\r?\n|$)", text[end:])
    if match is None:
        return None
    block_end = end + match.end()
    try:
        # A providers key in a nested table (or a multiline string) is not the
        # root assignment emitted by the affected saver.
        prefix = tomllib.loads(text[: start.start()] + "providers = []\n")
        if prefix.get("providers") != []:
            return None
    except tomllib.TOMLDecodeError:
        return None
    try:
        # A valid inline array must not be rewritten for an unrelated error.
        tomllib.loads(text[start.start() : block_end])
    except tomllib.TOMLDecodeError:
        pass
    else:
        return None

    content = text[start.end() : end]
    trivia = re.compile(r"(?:\s|#[^\r\n]*)*")
    assignment = re.compile(r'(\w+)[ \t]*=[ \t]*("(?:[^"\\\r\n]|\\.)*")')
    entries: list[list[str]] = [[]]
    pos = 0
    while pos < len(content):
        pos = trivia.match(content, pos).end()  # type: ignore[union-attr]
        if pos == len(content):
            break
        if content[pos] == ",":
            if not entries[-1]:
                return None
            entries.append([])
            pos += 1
            continue
        pair = assignment.match(content, pos)
        if pair is None:
            return None
        entries[-1].append(f"{pair[1]} = {pair[2]}")
        pos = pair.end()
        # Assignments require a newline (or a provider separator), not merely
        # whitespace between adjacent values on the same line.
        gap_end = trivia.match(content, pos).end()  # type: ignore[union-attr]
        if (
            gap_end < len(content)
            and content[gap_end] != ","
            and "\n" not in content[pos:gap_end]
        ):
            return None

    if not entries[-1]:
        return None
    replacement = "\n\n".join("[[providers]]\n" + "\n".join(entry) for entry in entries)
    # Appending tables avoids absorbing trailing root settings into a provider.
    candidate = (
        (text[: start.start()] + text[block_end:]).rstrip("\n")
        + "\n"
        + replacement
        + "\n"
    )
    try:
        original_settings = tomllib.loads(text[: start.start()] + text[block_end:])
        repaired = tomllib.loads(candidate)
        providers = repaired.pop("providers")
        if repaired != original_settings:
            return None
        from .models import ProviderConfig

        for provider in providers:
            ProviderConfig(**provider)
    except (tomllib.TOMLDecodeError, TypeError, KeyError):
        return None
    return candidate


def detect_config_problems(config_root: Path | None = None) -> list[ConfigProblem]:
    """Scan user config and cached Device Flow tokens without writing anything.

    With an explicit root, scan all cloud caches in its ``auth/`` directory.
    Otherwise select the current cache or first existing legacy cache, matching
    the precedence in ``llm_gptme._load_token``. Unreadable files are problems,
    not a clean result.
    """
    from .user import get_user_config_paths

    problems: list[ConfigProblem] = []
    if config_root is not None:
        config_path = config_root / "config.toml"
        token_paths = sorted((config_root / "auth").glob("gptme-cloud-*.json"))
    else:
        from ..llm.llm_gptme import _LEGACY_SERVICE_URLS, _get_token_path

        config_path, _ = get_user_config_paths()
        candidates = [_get_token_path()] + [
            _get_token_path(url) for url in _LEGACY_SERVICE_URLS
        ]
        token_paths = next(([path] for path in candidates if path.exists()), [])

    if config_path.exists():
        try:
            text = config_path.read_text(encoding="utf-8")
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                if _repaired_providers_text(text) is not None:
                    problems.append(
                        ConfigProblem(
                            name="providers-not-array",
                            path=config_path,
                            description=(
                                f"{config_path}: providers key contains table "
                                "syntax inside square brackets — not valid TOML. "
                                "Should be [[providers]] array-of-tables entries."
                            ),
                            repair_cmd="gptme --check-config --fix",
                        )
                    )
                else:
                    problems.append(
                        ConfigProblem(
                            name="config-invalid",
                            path=config_path,
                            description=f"{config_path}: invalid TOML; no safe automatic repair recognized.",
                            repair_cmd=f"Edit {shlex.quote(str(config_path))} to correct the TOML syntax",
                        )
                    )
        except (OSError, UnicodeError) as exc:
            problems.append(
                ConfigProblem(
                    name="config-unreadable",
                    path=config_path,
                    description=f"{config_path}: could not read config: {exc}",
                    repair_cmd=f"Check permissions and encoding of {shlex.quote(str(config_path))}",
                )
            )

    # Cached Device Flow token root must be an object (gptme/gptme#4797).
    for token_path in token_paths:
        try:
            data = json.loads(token_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                continue
            detail = f"not a JSON object (got {type(data).__name__})"
        except (ValueError, RecursionError, OSError, UnicodeError):
            # Do not echo JSON content: a decoder error may contain credentials.
            detail = "unreadable or invalid JSON"
        problems.append(
            ConfigProblem(
                name="auth-cache-invalid",
                path=token_path,
                description=f"{token_path}: cached auth token is {detail}; login state is broken.",
                repair_cmd=f"rm -- {shlex.quote(str(token_path))}  # then re-login: gptme-auth login",
            )
        )
    return problems


def fix_providers_corruption(config_path: Path) -> Path:
    """Validate a known repair, back up the original, then write the result.

    Raises ``ValueError`` without writing when no safe repair is recognized.
    Returns the backup path only after the validated content has been written.
    """
    import datetime
    import os
    import tempfile
    from pathlib import Path

    # Publish to the managed target, never replace a config symlink itself.
    config_path = config_path.resolve(strict=True)
    original_stat = config_path.stat()
    original = config_path.read_bytes()
    text = original.decode("utf-8")
    fixed_text = _repaired_providers_text(text)
    if fixed_text is None:
        raise ValueError("no safe providers repair recognized")

    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    bak = config_path.with_name(config_path.name + f".{ts}.bak")
    # Credentials must be private from creation, not chmod'd after copying.
    fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as backup:
        backup.write(original)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=config_path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(fixed_text)
            output.flush()
            os.fsync(output.fileno())
        # Detect an edit/replacement during preparation. This is optimistic
        # conflict detection, not a lock against an uncooperative editor writing
        # in the final check-to-replace interval.
        current_stat = config_path.stat()
        if (
            current_stat.st_ino != original_stat.st_ino
            or current_stat.st_mtime_ns != original_stat.st_mtime_ns
            or config_path.read_bytes() != original
        ):
            raise ValueError(
                "configuration changed during repair; retry after editing finishes"
            )
        os.replace(temporary, config_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return bak
