#!/usr/bin/env python3
"""CI self-heal script: analyze test failures and gate safe auto-fixes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

WHITELISTED_FAILURE_CLASSES = frozenset(
    {
        "syntax_error",
        "import_error",
        "missing_symbol",
        "renamed_symbol",
        "fixture_typo",
        "assertion_literal_update",
        "test_selector_typo",
    }
)


@dataclass(frozen=True)
class SelfHealAnalysis:
    root_cause: str
    proposed_fix: str
    confidence: str
    failure_class: str
    patch: str
    validation_commands: list[str]
    risk_notes: list[str]


@dataclass(frozen=True)
class PatchStats:
    files: int
    changed_lines: int
    paths: list[str]
    has_deletions: bool = False
    has_binary: bool = False
    has_mode_changes: bool = False


@dataclass(frozen=True)
class GateConfig:
    repository: str
    allowed_authors: frozenset[str] = frozenset({"TimeToBuildBob"})
    max_files: int = 3
    max_changed_lines: int = 40
    forbidden_prefixes: tuple[str, ...] = (".github/", "docs/")
    forbidden_paths: frozenset[str] = frozenset({"scripts/github_ci_self_heal.py"})
    require_validation: bool = True


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reason: str
    reasons: list[str]
    patch_stats: PatchStats
    validation_commands: list[str]


def analyze_failure(failure_log: str, pr_diff: str) -> str:
    """Call Claude to analyze CI failure and propose a minimal fix."""
    # Trim inputs to stay within reasonable token limits
    failure_log = failure_log[-12000:] if len(failure_log) > 12000 else failure_log
    pr_diff = pr_diff[:8000] if len(pr_diff) > 8000 else pr_diff

    prompt = f"""A CI test failure occurred on a pull request. Analyze the root cause and propose a minimal fix.

## Test failure output (last portion):
~~~~
{failure_log}
~~~~

## Pull request diff:
~~~~diff
{pr_diff}
~~~~

Respond in this exact format:

**Root cause**: (1-2 sentences identifying exactly what broke and why)

**Proposed fix**: (minimal code change as a unified diff, or a clear explanation if the fix is not straightforward)

**Confidence**: high / medium / low — and why"""

    return _call_claude(prompt, max_tokens=1024)


def analyze_failure_structured(
    failure_log: str, pr_diff: str
) -> SelfHealAnalysis | None:
    """Call Claude for structured analysis used by the auto-fix gate."""
    failure_log = failure_log[-12000:] if len(failure_log) > 12000 else failure_log
    pr_diff = pr_diff[:8000] if len(pr_diff) > 8000 else pr_diff

    prompt = f"""A CI test failure occurred on a pull request. Analyze the root cause and propose a minimal fix.

Return only JSON matching this schema:
{{
  "root_cause": "short explanation",
  "proposed_fix": "short explanation",
  "confidence": "high|medium|low",
  "failure_class": "syntax_error|import_error|missing_symbol|renamed_symbol|fixture_typo|assertion_literal_update|test_selector_typo|other",
  "patch": "unified diff, or empty string if no safe patch",
  "validation_commands": ["targeted command to validate the fix"],
  "risk_notes": ["short risk note"]
}}

Only use confidence "high" when the fix is mechanical and directly supported by the log and diff.

## Test failure output (last portion):
~~~~
{failure_log}
~~~~

## Pull request diff:
~~~~diff
{pr_diff}
~~~~"""

    response = _call_claude(prompt, max_tokens=2048)
    if not response.strip():
        return None
    try:
        return parse_analysis_json(response)
    except ValueError as exc:
        return SelfHealAnalysis(
            root_cause="Model returned malformed structured analysis.",
            proposed_fix=response[:2000],
            confidence="low",
            failure_class="other",
            patch="",
            validation_commands=[],
            risk_notes=[str(exc)],
        )


def _call_claude(prompt: str, *, max_tokens: int) -> str:
    import anthropic

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        print(
            "Warning: ANTHROPIC_API_KEY is invalid or expired — skipping analysis.",
            file=sys.stderr,
        )
        return "⚠️ Self-heal analysis skipped: the `ANTHROPIC_API_KEY` repository secret is invalid or expired. A maintainer needs to update it in **Settings → Secrets and variables → Actions**."
    except anthropic.APIError as e:
        print(
            f"Warning: Anthropic API error ({type(e).__name__}) — skipping analysis.",
            file=sys.stderr,
        )
        return ""

    from anthropic.types import TextBlock

    text_blocks = [b for b in message.content if isinstance(b, TextBlock)]
    return text_blocks[0].text if text_blocks else ""


def parse_analysis_json(text: str) -> SelfHealAnalysis:
    try:
        data = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid analysis JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise ValueError("analysis JSON must be an object")

    root_cause = _required_str(data, "root_cause")
    proposed_fix = _required_str(data, "proposed_fix")
    confidence = _required_str(data, "confidence").lower()
    failure_class = _required_str(data, "failure_class").lower()
    patch = _required_str(data, "patch")
    validation_commands = _str_list(data.get("validation_commands", []))
    risk_notes = _str_list(data.get("risk_notes", []))
    return SelfHealAnalysis(
        root_cause=root_cause,
        proposed_fix=proposed_fix,
        confidence=confidence,
        failure_class=failure_class,
        patch=patch,
        validation_commands=validation_commands,
        risk_notes=risk_notes,
    )


def render_analysis_markdown(analysis: SelfHealAnalysis) -> str:
    lines = [
        f"**Root cause**: {analysis.root_cause}",
        "",
        f"**Proposed fix**: {analysis.proposed_fix}",
        "",
        f"**Confidence**: {analysis.confidence}",
        "",
        f"**Failure class**: {analysis.failure_class}",
    ]
    if analysis.patch.strip():
        lines.extend(["", "```diff", analysis.patch.rstrip(), "```"])
    if analysis.validation_commands:
        lines.extend(["", "**Validation**:"])
        lines.extend(f"- `{command}`" for command in analysis.validation_commands)
    if analysis.risk_notes:
        lines.extend(["", "**Risk notes**:"])
        lines.extend(f"- {note}" for note in analysis.risk_notes)
    return "\n".join(lines)


def changed_paths_from_diff(diff_text: str) -> set[str]:
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        for raw_path in parts[2:4]:
            path = _normalize_diff_path(raw_path)
            if path and path != "/dev/null":
                paths.add(path)
    return paths


def patch_stats(patch: str) -> PatchStats:
    paths: set[str] = set()
    changed_lines = 0
    has_deletions = False
    has_binary = False
    has_mode_changes = False

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                for raw_path in parts[2:4]:
                    path = _normalize_diff_path(raw_path)
                    if path and path != "/dev/null":
                        paths.add(path)
        elif line.startswith("deleted file mode"):
            has_deletions = True
        elif line.startswith(("old mode ", "new mode ")):
            has_mode_changes = True
        elif line.startswith(("Binary files ", "GIT binary patch")):
            has_binary = True
        elif line.startswith(("+++", "---")):
            if line.endswith("/dev/null"):
                has_deletions = True
        elif line.startswith(("+", "-")):
            changed_lines += 1

    return PatchStats(
        files=len(paths),
        changed_lines=changed_lines,
        paths=sorted(paths),
        has_deletions=has_deletions,
        has_binary=has_binary,
        has_mode_changes=has_mode_changes,
    )


def evaluate_autofix_gate(
    analysis: SelfHealAnalysis,
    pr_metadata: dict[str, object],
    pr_diff: str,
    config: GateConfig,
    *,
    repo_root: Path | None = None,
) -> GateResult:
    stats = patch_stats(analysis.patch)
    reasons: list[str] = []

    if analysis.confidence != "high":
        reasons.append(f"confidence is {analysis.confidence!r}, not 'high'")
    if analysis.failure_class not in WHITELISTED_FAILURE_CLASSES:
        reasons.append(f"failure_class {analysis.failure_class!r} is not whitelisted")
    if not analysis.patch.strip():
        reasons.append("analysis patch is empty")
    if config.require_validation and not analysis.validation_commands:
        reasons.append("analysis has no validation commands")

    author = _metadata_author_login(pr_metadata)
    if author not in config.allowed_authors:
        reasons.append(f"PR author {author!r} is not allowlisted")

    if _metadata_head_repository(pr_metadata) != config.repository:
        reasons.append("PR head repository is not the base repository")

    if stats.files > config.max_files:
        reasons.append(f"patch touches {stats.files} files, max is {config.max_files}")
    if stats.changed_lines > config.max_changed_lines:
        reasons.append(
            f"patch changes {stats.changed_lines} non-context lines, "
            f"max is {config.max_changed_lines}"
        )
    if stats.has_deletions:
        reasons.append("patch deletes a file")
    if stats.has_binary:
        reasons.append("patch contains binary changes")
    if stats.has_mode_changes:
        reasons.append("patch changes file modes")

    pr_paths = changed_paths_from_diff(pr_diff)
    patch_paths = set(stats.paths)
    if patch_paths and not patch_paths.issubset(pr_paths):
        outside = sorted(patch_paths - pr_paths)
        reasons.append(f"patch touches paths outside the PR diff: {outside}")

    forbidden_paths = _forbidden_patch_paths(patch_paths, config)
    if forbidden_paths:
        reasons.append(f"patch touches forbidden paths: {forbidden_paths}")

    if repo_root is not None and analysis.patch.strip():
        apply_error = _git_apply_check(repo_root, analysis.patch)
        if apply_error:
            reasons.append(apply_error)

    return GateResult(
        eligible=not reasons,
        reason="eligible" if not reasons else reasons[0],
        reasons=reasons,
        patch_stats=stats,
        validation_commands=analysis.validation_commands,
    )


def gate_result_to_json(result: GateResult) -> dict[str, object]:
    return {
        "eligible": result.eligible,
        "reason": result.reason,
        "reasons": result.reasons,
        "patch_stats": asdict(result.patch_stats),
        "validation_commands": result.validation_commands,
    }


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1] == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"analysis field {key!r} must be a string")
    return value


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("analysis list fields must be arrays of strings")
    return value


def _normalize_diff_path(raw_path: str) -> str:
    path = raw_path.strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _metadata_author_login(pr_metadata: dict[str, object]) -> str:
    author = pr_metadata.get("author")
    if isinstance(author, dict):
        login = author.get("login")
        if isinstance(login, str):
            return login
    return ""


def _metadata_head_repository(pr_metadata: dict[str, object]) -> str:
    owner_login = ""
    owner = pr_metadata.get("headRepositoryOwner")
    if isinstance(owner, dict):
        login = owner.get("login")
        if isinstance(login, str):
            owner_login = login

    repo_name = ""
    repo = pr_metadata.get("headRepository")
    if isinstance(repo, dict):
        name = repo.get("name")
        if isinstance(name, str):
            repo_name = name
        repo_owner = repo.get("owner")
        if not owner_login and isinstance(repo_owner, dict):
            login = repo_owner.get("login")
            if isinstance(login, str):
                owner_login = login

    return f"{owner_login}/{repo_name}" if owner_login and repo_name else ""


def _forbidden_patch_paths(paths: set[str], config: GateConfig) -> list[str]:
    return [
        path
        for path in sorted(paths)
        if path in config.forbidden_paths or path.startswith(config.forbidden_prefixes)
    ]


def _git_apply_check(repo_root: Path, patch: str) -> str | None:
    result = subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch,
        text=True,
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout).strip().splitlines()
    tail = detail[-1] if detail else "unknown error"
    return f"git apply --check failed: {tail}"


def _read_optional_file(path: Path, missing_text: str) -> str:
    return path.read_text(errors="replace") if path.exists() else missing_text


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _default_gate_config() -> GateConfig:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    authors = os.environ.get("SELF_HEAL_AUTOFIX_AUTHORS", "TimeToBuildBob")
    allowed_authors = frozenset(
        author.strip() for author in authors.split(",") if author.strip()
    )
    return GateConfig(repository=repository, allowed_authors=allowed_authors)


def _legacy_main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <failure_log_path> <pr_diff_path>", file=sys.stderr
        )
        return 1

    failure_log_path = Path(argv[0])
    pr_diff_path = Path(argv[1])

    failure_log = (
        failure_log_path.read_text(errors="replace")
        if failure_log_path.exists()
        else "(no failure log available)"
    )
    pr_diff = (
        pr_diff_path.read_text(errors="replace")
        if pr_diff_path.exists() and pr_diff_path.stat().st_size > 0
        else "(no diff available)"
    )

    if not failure_log_path.exists():
        print(f"Warning: failure log not found at {failure_log_path}", file=sys.stderr)
    if not pr_diff_path.exists():
        print(f"Warning: PR diff not found at {pr_diff_path}", file=sys.stderr)

    analysis = analyze_failure(failure_log, pr_diff)
    if not analysis.strip():
        print("Warning: no analysis produced — skipping comment.", file=sys.stderr)
        return 0
    print(analysis)
    return 0


def _analyze_main(args: argparse.Namespace) -> int:
    failure_log_path = Path(args.failure_log)
    pr_diff_path = Path(args.pr_diff)
    failure_log = _read_optional_file(failure_log_path, "(no failure log available)")
    pr_diff = _read_optional_file(pr_diff_path, "(no diff available)")

    analysis = analyze_failure_structured(failure_log, pr_diff)
    if analysis is None:
        print("Warning: no analysis produced — skipping output.", file=sys.stderr)
        return 0

    if args.json_out:
        _write_text(Path(args.json_out), json.dumps(asdict(analysis), indent=2) + "\n")
    markdown = render_analysis_markdown(analysis)
    if args.markdown_out:
        _write_text(Path(args.markdown_out), markdown + "\n")
    if not args.json_out and not args.markdown_out:
        print(markdown)
    return 0


def _gate_main(args: argparse.Namespace) -> int:
    analysis = parse_analysis_json(Path(args.analysis_json).read_text())
    pr_metadata = json.loads(Path(args.pr_metadata_json).read_text())
    if not isinstance(pr_metadata, dict):
        raise SystemExit("PR metadata JSON must be an object")
    pr_diff = Path(args.pr_diff).read_text(errors="replace")

    config = _default_gate_config()
    if args.repository:
        config = GateConfig(
            repository=args.repository,
            allowed_authors=config.allowed_authors,
        )
    repo_root = Path(args.repo_root) if args.repo_root else None
    result = evaluate_autofix_gate(
        analysis, pr_metadata, pr_diff, config, repo_root=repo_root
    )
    payload = json.dumps(gate_result_to_json(result), indent=2) + "\n"
    if args.json_out:
        _write_text(Path(args.json_out), payload)
    else:
        print(payload, end="")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("failure_log")
    analyze_parser.add_argument("pr_diff")
    analyze_parser.add_argument("--json-out")
    analyze_parser.add_argument("--markdown-out")
    analyze_parser.set_defaults(func=_analyze_main)

    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("analysis_json")
    gate_parser.add_argument("pr_metadata_json")
    gate_parser.add_argument("pr_diff")
    gate_parser.add_argument("--json-out")
    gate_parser.add_argument("--repository")
    gate_parser.add_argument("--repo-root")
    gate_parser.set_defaults(func=_gate_main)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in {"analyze", "gate"}:
        return _legacy_main(argv)

    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
