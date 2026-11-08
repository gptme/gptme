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


def detect_config_problems(config_root: Path | None = None) -> list[ConfigProblem]:
    """Scan the resolved user config root for known corruption patterns.

    Never writes anything.  Never raises on unreadable files — an unreadable
    config is reported as a problem rather than a crash.

    *config_root*: when given, the directory that contains ``config.toml`` and
    the ``auth/`` subdirectory (e.g. ``~/.config/gptme``).  When omitted the
    standard user paths from :func:`~gptme.config.user.get_user_config_paths`
    and :func:`~gptme.llm.llm_gptme._get_token_path` are used.
    """
    problems: list[ConfigProblem] = []

    # --- resolve paths -------------------------------------------------------
    if config_root is not None:
        config_path = config_root / "config.toml"
        # Token path uses the same hash as _get_token_path; approximate it by
        # globbing the auth dir so we don't duplicate the hash logic.
        auth_dir = config_root / "auth"
        try:
            token_candidates = list(auth_dir.glob("gptme-cloud-*.json"))
            token_path: Path | None = token_candidates[0] if token_candidates else None
        except OSError:
            token_path = None
    else:
        try:
            from .user import get_user_config_paths

            config_path, _ = get_user_config_paths()
        except Exception:
            return problems
        try:
            from ..llm.llm_gptme import _get_token_path

            token_path = _get_token_path()
        except Exception:
            token_path = None

    # --- pattern 1: providers-not-array --------------------------------------
    # config.toml fails to parse AND contains a `providers = [` line that is
    # not a valid inline array — the exact shape written by the first no-key
    # custom-provider setup (gptme/gptme#4806).
    if config_path.exists():
        try:
            text = config_path.read_text(encoding="utf-8")
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                if any(
                    ln.lstrip().startswith("providers = [") for ln in text.splitlines()
                ):
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
        except (OSError, UnicodeError) as exc:
            problems.append(
                ConfigProblem(
                    name="config-unreadable",
                    path=config_path,
                    description=f"{config_path}: could not read config: {exc}",
                    repair_cmd=f"rm {config_path}  # then restart gptme to regenerate",
                )
            )

    # --- pattern 2: auth-cache-invalid ---------------------------------------
    # Cached Device Flow token root is not an object (list / null).
    # Also catches a JSON parse failure on the token file.
    # (gptme/gptme#4797)
    if token_path and token_path.exists():
        try:
            data = json.loads(token_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                problems.append(
                    ConfigProblem(
                        name="auth-cache-invalid",
                        path=token_path,
                        description=(
                            f"{token_path}: cached auth token is not a JSON object "
                            f"(got {type(data).__name__}); login state is broken."
                        ),
                        repair_cmd=(
                            f"rm {token_path}  # then re-login: gptme-auth login"
                        ),
                    )
                )
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            problems.append(
                ConfigProblem(
                    name="auth-cache-invalid",
                    path=token_path,
                    description=(
                        f"{token_path}: cached auth token cannot be parsed: {exc}"
                    ),
                    repair_cmd=(f"rm {token_path}  # then re-login: gptme-auth login"),
                )
            )

    return problems


def fix_providers_corruption(config_path: Path) -> Path:
    """Repair the ``providers-not-array`` corruption in place.

    Rewrites *config_path* so that the ``providers = [...]`` block becomes
    proper ``[[providers]]`` array-of-tables entries.  An unrelated setting
    is preserved.  A timestamped ``.bak`` backup is created before writing.

    Returns the path to the backup file.
    """
    import datetime
    import shutil

    text = config_path.read_text(encoding="utf-8")

    # Timestamped backup
    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = config_path.with_name(config_path.name + f".{ts}.bak")
    shutil.copy2(config_path, bak)

    # Match the multi-line corrupt block: `providers = [<content>\n]`
    block_re = re.compile(r"providers\s*=\s*\[(.*?)\n\]", re.DOTALL)
    m = block_re.search(text)
    if not m:
        # Pattern not found; backup was written but file is unchanged.
        return bak

    inner = m.group(1).strip()

    # Split on commas that immediately precede a new key-value line.
    # Handles both single-entry and multi-entry corrupt arrays.
    entries_raw = re.split(r",\s*(?=\w+\s*=)", inner)

    aot_parts: list[str] = []
    for raw in entries_raw:
        raw = raw.strip().rstrip(",").strip()
        if not raw:
            continue
        pairs: list[str] = []
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if "=" in line:
                pairs.append(line)
        if pairs:
            aot_parts.append("[[providers]]\n" + "\n".join(pairs))

    replacement = "\n\n".join(aot_parts)
    fixed_text = text[: m.start()] + replacement + text[m.end() :]
    config_path.write_text(fixed_text, encoding="utf-8")
    return bak
