"""Deterministically verify factual claims before acting."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from ..message import Message
from .base import Parameter, ToolFormat, ToolFunction, ToolSpec, ToolUse

PROCESS_VERIFICATION_ENV = "GPTME_VERIFY_ALLOW_PROCESS"
DEFAULT_TIMEOUT_SECONDS = 30.0
TEST_TIMEOUT_SECONDS = 60.0
MAX_FILE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 4000
CLAIM_TYPES = (
    "file_exists",
    "file_not_exists",
    "contains",
    "not_contains",
    "shell",
    "env_var",
    "test_passes",
    "test_fails",
)


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    claim_type: str
    target: str
    expected: str | None = None
    actual: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def __str__(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _result(
    ok: bool,
    claim_type: str,
    target: str,
    reason: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
) -> VerificationResult:
    return VerificationResult(ok, claim_type, target, expected, actual, reason)


def _display_path(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def verify_file_exists(path: str) -> VerificationResult:
    """Verify that a filesystem path exists."""
    if not path:
        return _result(False, "file_exists", "", "path is required")
    display = _display_path(path)
    ok = Path(path).expanduser().exists()
    reason = f"path exists: {display}" if ok else f"path does not exist: {display}"
    return _result(ok, "file_exists", path, reason, actual=display)


def verify_file_not_exists(path: str) -> VerificationResult:
    """Verify that a filesystem path does not exist."""
    if not path:
        return _result(False, "file_not_exists", "", "path is required")
    display = _display_path(path)
    ok = not Path(path).expanduser().exists()
    reason = f"path is absent: {display}" if ok else f"path exists: {display}"
    return _result(ok, "file_not_exists", path, reason, actual=display)


def _read_text(
    path: str, claim_type: str
) -> tuple[str | None, VerificationResult | None]:
    if not path:
        return None, _result(False, claim_type, "", "path is required")

    candidate = Path(path).expanduser()
    display = _display_path(path)
    if not candidate.exists():
        return None, _result(
            False, claim_type, path, f"path does not exist: {display}", actual=display
        )
    if not candidate.is_file():
        return None, _result(
            False, claim_type, path, f"path is not a file: {display}", actual=display
        )

    try:
        size = candidate.stat().st_size
        if size > MAX_FILE_BYTES:
            return None, _result(
                False,
                claim_type,
                path,
                f"file is too large to scan ({size} bytes, max {MAX_FILE_BYTES})",
                actual=f"{size} bytes",
            )
        return candidate.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, _result(False, claim_type, path, "file is not valid UTF-8 text")
    except OSError as exc:
        return None, _result(False, claim_type, path, f"could not read file: {exc}")


def _match_line(text: str, match: re.Match[str]) -> str:
    line_no = text.count("\n", 0, match.start()) + 1
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    line = text[line_start : len(text) if line_end == -1 else line_end].strip()
    return f"line {line_no}: {line[:197] + '...' if len(line) > 200 else line}"


def _verify_pattern(
    path: str,
    pattern: str,
    *,
    claim_type: str,
    want_found: bool,
) -> VerificationResult:
    if not pattern:
        return _result(False, claim_type, path, "pattern is required")

    text, error = _read_text(path, claim_type)
    if error is not None:
        return error
    assert text is not None

    try:
        match = re.search(pattern, text, flags=re.MULTILINE)
    except re.error as exc:
        return _result(
            False,
            claim_type,
            path,
            f"invalid regex pattern: {exc}",
            expected=pattern,
        )

    found = match is not None
    ok = found is want_found
    actual = _match_line(text, match) if match else "not found"
    reason = "pattern was found" if found else "pattern was not found"
    return _result(ok, claim_type, path, reason, expected=pattern, actual=actual)


def verify_contains(path: str, pattern: str) -> VerificationResult:
    """Verify that a UTF-8 text file contains a regex pattern."""
    return _verify_pattern(path, pattern, claim_type="contains", want_found=True)


def verify_not_contains(path: str, pattern: str) -> VerificationResult:
    """Verify that a UTF-8 text file does not contain a regex pattern."""
    return _verify_pattern(path, pattern, claim_type="not_contains", want_found=False)


def _process_verification_allowed() -> bool:
    if os.environ.get(PROCESS_VERIFICATION_ENV, "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    try:
        from . import has_tool
    except Exception:
        return False
    return has_tool("shell")


def _timeout(value: str | float | int | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.1, min(parsed, 300.0))


def _short(value: str) -> str:
    value = value.strip()
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    return value[: MAX_OUTPUT_CHARS - 15] + "\n...[truncated]"


def _process_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = [_short(result.stdout or "")]
    if result.stderr:
        parts.append(f"stderr:\n{_short(result.stderr)}")
    return "\n".join(part for part in parts if part) or "<no output>"


def _run_process(
    args: list[str],
    *,
    claim_type: str,
    target: str,
    timeout: float,
    expected: str | None = None,
) -> tuple[subprocess.CompletedProcess[str] | None, VerificationResult | None]:
    if not _process_verification_allowed():
        return None, _result(
            False,
            claim_type,
            target,
            f"process checks require the shell tool or {PROCESS_VERIFICATION_ENV}=1",
            expected=expected,
        )
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout
        ), None
    except FileNotFoundError as exc:
        return None, _result(
            False, claim_type, target, f"executable not found: {exc.filename}"
        )
    except subprocess.TimeoutExpired:
        return None, _result(
            False, claim_type, target, f"command timed out after {timeout:g}s"
        )
    except OSError as exc:
        return None, _result(False, claim_type, target, f"could not run command: {exc}")


def verify_shell(
    command: str,
    expected: str | None = None,
    timeout: str | float | int | None = None,
) -> VerificationResult:
    """Verify that a process command exits 0 and optionally prints expected text."""
    if not command:
        return _result(False, "shell", "", "command is required")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return _result(
            False,
            "shell",
            command,
            f"could not parse command: {exc}",
            expected=expected,
        )
    if not args:
        return _result(False, "shell", command, "command is empty", expected=expected)

    proc, error = _run_process(
        args,
        claim_type="shell",
        target=command,
        timeout=_timeout(timeout, DEFAULT_TIMEOUT_SECONDS),
        expected=expected,
    )
    if error is not None:
        return error
    assert proc is not None

    actual = _process_output(proc)
    if proc.returncode != 0:
        return _result(
            False,
            "shell",
            command,
            f"command exited {proc.returncode}",
            expected=expected,
            actual=actual,
        )
    if expected is not None and expected not in proc.stdout:
        return _result(
            False,
            "shell",
            command,
            "expected text was not present in stdout",
            expected=expected,
            actual=actual,
        )
    return _result(
        True, "shell", command, "command exited 0", expected=expected, actual=actual
    )


def verify_env_var(name: str, expected: str | None = None) -> VerificationResult:
    """Verify that an environment variable is set, optionally to an exact value."""
    if not name:
        return _result(False, "env_var", "", "environment variable name is required")
    if name not in os.environ:
        return _result(
            False,
            "env_var",
            name,
            "environment variable is not set",
            expected=expected,
            actual="<unset>",
        )
    if expected is not None and os.environ[name] != expected:
        return _result(
            False,
            "env_var",
            name,
            "environment variable is set but does not match expected value",
            expected=expected,
            actual="<set but different>",
        )
    return _result(
        True,
        "env_var",
        name,
        "environment variable is set",
        expected=expected,
        actual="<set>",
    )


def _verify_pytest(
    test_spec: str,
    claim_type: str,
    timeout: str | float | int | None,
) -> VerificationResult:
    if not test_spec:
        return _result(False, claim_type, "", "test_spec is required")
    if test_spec.startswith("-"):
        return _result(
            False, claim_type, test_spec, "test_spec must not start with '-'"
        )

    proc, error = _run_process(
        [sys.executable, "-m", "pytest", test_spec],
        claim_type=claim_type,
        target=test_spec,
        timeout=_timeout(timeout, TEST_TIMEOUT_SECONDS),
    )
    if error is not None:
        return error
    assert proc is not None

    actual = f"exit_code={proc.returncode}\n{_process_output(proc)}"
    if claim_type == "test_passes":
        reason = "pytest passed" if proc.returncode == 0 else "pytest did not pass"
        return _result(
            proc.returncode == 0, claim_type, test_spec, reason, actual=actual
        )
    if proc.returncode == 1:
        return _result(
            True, claim_type, test_spec, "pytest failed as expected", actual=actual
        )
    reason = (
        "pytest passed unexpectedly"
        if proc.returncode == 0
        else f"pytest exited {proc.returncode}, not the expected failure code 1"
    )
    return _result(False, claim_type, test_spec, reason, actual=actual)


def verify_test_passes(
    test_spec: str, timeout: str | float | int | None = None
) -> VerificationResult:
    """Verify that a pytest test spec passes."""
    return _verify_pytest(test_spec, "test_passes", timeout)


def verify_test_fails(
    test_spec: str, timeout: str | float | int | None = None
) -> VerificationResult:
    """Verify that a pytest test spec fails with pytest exit code 1."""
    return _verify_pytest(test_spec, "test_fails", timeout)


def verify_claim(
    claim_type: str,
    target: str | None = None,
    expected: str | None = None,
    pattern: str | None = None,
    command: str | None = None,
    name: str | None = None,
    timeout: str | float | int | None = None,
) -> VerificationResult:
    """Dispatch a deterministic claim verification by type."""
    kind = claim_type.strip()
    if kind == "file_exists":
        return verify_file_exists(target or "")
    if kind == "file_not_exists":
        return verify_file_not_exists(target or "")
    if kind == "contains":
        return verify_contains(
            target or "", pattern if pattern is not None else expected or ""
        )
    if kind == "not_contains":
        return verify_not_contains(
            target or "", pattern if pattern is not None else expected or ""
        )
    if kind == "shell":
        return verify_shell(command or target or "", expected=expected, timeout=timeout)
    if kind == "env_var":
        return verify_env_var(name or target or "", expected=expected)
    if kind == "test_passes":
        return verify_test_passes(target or "", timeout=timeout)
    if kind == "test_fails":
        return verify_test_fails(target or "", timeout=timeout)
    return _result(
        False,
        kind or "unknown",
        target or command or name or "",
        f"unknown claim_type: {kind}",
    )


def _extract_values(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> dict[str, str]:
    if kwargs:
        return dict(kwargs)
    if args:
        return {
            "type": args[0],
            **({"target": args[1]} if len(args) >= 2 else {}),
            **({"expected": " ".join(args[2:])} if len(args) >= 3 else {}),
        }
    if not code or not code.strip():
        return {}

    stripped = code.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return {str(key): str(value) for key, value in parsed.items()}

    values: dict[str, str] = {}
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        delimiter = ":" if ":" in line else "=" if "=" in line else None
        if delimiter:
            key, value = line.split(delimiter, 1)
            values[key.strip()] = value.strip()
    return values


def execute_verify_claim(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Message:
    values = _extract_values(code, args, kwargs)
    result = verify_claim(
        claim_type=values.get("claim_type") or values.get("type") or "",
        target=values.get("target"),
        expected=values.get("expected"),
        pattern=values.get("pattern"),
        command=values.get("command"),
        name=values.get("name"),
        timeout=values.get("timeout"),
    )
    return Message("system", str(result))


def examples(tool_format: str) -> str:
    output_format = cast(ToolFormat, tool_format)
    file_call = ToolUse(
        "verify", [], "type: file_exists\ntarget: gptme/tools/bash.py"
    ).to_output(output_format)
    shell_call = ToolUse(
        "verify",
        [],
        "type: shell\ncommand: git branch --show-current\nexpected: master",
    ).to_output(output_format)
    return f"""
> User: Verify that a file exists before patching it
> Assistant:
{file_call}
> System: {{ "ok": true, "claim_type": "file_exists" }}

> User: Verify the current branch
> Assistant:
{shell_call}
> System: {{ "ok": true, "claim_type": "shell" }}
""".strip()


_PARAMS = [
    Parameter(
        "type",
        'Literal["file_exists", "file_not_exists", "contains", "not_contains", '
        '"shell", "env_var", "test_passes", "test_fails"]',
        "Claim type to verify.",
        required=True,
    ),
    Parameter("target", "string", "Path, env var name, command, or pytest spec."),
    Parameter("pattern", "string", "Regex for contains/not_contains checks."),
    Parameter("expected", "string", "Expected stdout text or env var value."),
    Parameter("command", "string", "Read-only process command for shell checks."),
    Parameter("name", "string", "Environment variable name for env_var checks."),
    Parameter("timeout", "number", "Process timeout in seconds, capped at 300."),
]


tool = ToolSpec(
    name="verify_claim",
    desc="Deterministically verify factual claims before taking action",
    instructions=f"""
Use before risky actions when a factual premise might be stale or hallucinated.
Prefer native claim types over shell checks.

Claim types: {", ".join(CLAIM_TYPES)}.
For contains/not_contains, set target to a UTF-8 text file and pattern to a regex.
For env_var, set name or target; actual values are never echoed.
For shell, set command; expected is optional stdout text.
For test_passes/test_fails, set target to a pytest test spec.

Process checks only run when the shell tool is loaded or
{PROCESS_VERIFICATION_ENV}=1 is set, so verify_claim cannot widen a restricted
session's execution capability.
""".strip(),
    instructions_format={
        "tool": (
            "Verify a factual premise. Required: type. Optional: target, pattern, "
            "expected, command, name, timeout. Claim types: "
            + ", ".join(CLAIM_TYPES)
            + f". Process checks require shell or {PROCESS_VERIFICATION_ENV}=1."
        )
    },
    examples=examples,
    functions=[ToolFunction.from_callable(verify_claim)],
    execute=execute_verify_claim,
    block_types=["verify"],
    parameters=_PARAMS,
    hints=frozenset({"read-only"}),
)

__doc__ = tool.get_doc(__doc__)
