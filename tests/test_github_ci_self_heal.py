from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/github_ci_self_heal.py"
spec = importlib.util.spec_from_file_location("github_ci_self_heal", MODULE_PATH)
assert spec is not None
github_ci_self_heal = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["github_ci_self_heal"] = github_ci_self_heal
spec.loader.exec_module(github_ci_self_heal)


def _analysis(
    *,
    confidence: str = "high",
    failure_class: str = "import_error",
    patch_path: str = "tests/test_example.py",
    validation_commands: list[str] | None = None,
) -> Any:
    return github_ci_self_heal.SelfHealAnalysis(
        root_cause="A test imports a renamed symbol.",
        proposed_fix="Update the import to the renamed symbol.",
        confidence=confidence,
        failure_class=failure_class,
        patch=(
            f"diff --git a/{patch_path} b/{patch_path}\n"
            f"--- a/{patch_path}\n"
            f"+++ b/{patch_path}\n"
            "@@ -1 +1 @@\n"
            "-from package import old_name\n"
            "+from package import new_name\n"
        ),
        validation_commands=validation_commands
        or ["uv run pytest tests/test_example.py -q"],
        risk_notes=[],
    )


def _pr_metadata(
    *, author: str = "TimeToBuildBob", owner: str = "gptme", repo: str = "gptme"
) -> dict[str, object]:
    return {
        "number": 123,
        "author": {"login": author},
        "headRepositoryOwner": {"login": owner},
        "headRepository": {"name": repo, "owner": {"login": owner}},
    }


def _pr_diff(path: str = "tests/test_example.py") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def _config() -> Any:
    return github_ci_self_heal.GateConfig(repository="gptme/gptme")


def test_parse_analysis_json_accepts_valid_structured_analysis() -> None:
    payload = {
        "root_cause": "Import used an old name.",
        "proposed_fix": "Update the import.",
        "confidence": "HIGH",
        "failure_class": "IMPORT_ERROR",
        "patch": _analysis().patch,
        "validation_commands": ["uv run pytest tests/test_example.py -q"],
        "risk_notes": ["mechanical import-only change"],
    }

    analysis = github_ci_self_heal.parse_analysis_json(json.dumps(payload))

    assert analysis.confidence == "high"
    assert analysis.failure_class == "import_error"
    assert analysis.validation_commands == ["uv run pytest tests/test_example.py -q"]


def test_parse_analysis_json_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="invalid analysis JSON"):
        github_ci_self_heal.parse_analysis_json("{not-json")


def test_gate_accepts_safe_high_confidence_same_repo_patch() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(),
        _pr_metadata(),
        _pr_diff(),
        _config(),
    )

    assert result.eligible
    assert result.reason == "eligible"
    assert result.patch_stats.changed_lines == 2


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_gate_rejects_non_high_confidence(confidence: str) -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(confidence=confidence),
        _pr_metadata(),
        _pr_diff(),
        _config(),
    )

    assert not result.eligible
    assert "not 'high'" in result.reason


def test_gate_rejects_non_whitelisted_failure_class() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(failure_class="behavior_change"),
        _pr_metadata(),
        _pr_diff(),
        _config(),
    )

    assert not result.eligible
    assert "not whitelisted" in result.reason


def test_gate_rejects_fork_pr_metadata() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(),
        _pr_metadata(owner="contributor"),
        _pr_diff(),
        _config(),
    )

    assert not result.eligible
    assert "head repository" in result.reasons[0]


def test_gate_rejects_non_allowlisted_author() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(),
        _pr_metadata(author="dependabot[bot]"),
        _pr_diff(),
        _config(),
    )

    assert not result.eligible
    assert "not allowlisted" in result.reason


def test_gate_rejects_patch_touching_paths_outside_pr_diff() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(patch_path="src/gptme/other.py"),
        _pr_metadata(),
        _pr_diff("tests/test_example.py"),
        _config(),
    )

    assert not result.eligible
    assert "outside the PR diff" in result.reason


def test_gate_rejects_forbidden_paths() -> None:
    result = github_ci_self_heal.evaluate_autofix_gate(
        _analysis(patch_path=".github/workflows/test.yml"),
        _pr_metadata(),
        _pr_diff(".github/workflows/test.yml"),
        _config(),
    )

    assert not result.eligible
    assert "forbidden paths" in result.reason


def test_render_analysis_markdown_escapes_backtick_fence() -> None:
    """Backtick sequences in model-provided patch must not break the fenced block."""
    analysis = github_ci_self_heal.SelfHealAnalysis(
        root_cause="root",
        proposed_fix="fix",
        confidence="high",
        failure_class="import_error",
        patch="diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-```\n+x\n",
        validation_commands=[],
        risk_notes=["note with\nnewline"],
    )
    md = github_ci_self_heal.render_analysis_markdown(analysis)
    # The triple-backtick in the patch must not close the outer fence prematurely.
    assert "```\n```" not in md
    # Risk note newline must be flattened.
    assert "note with\nnewline" not in md
    assert "note with newline" in md


def test_parse_diff_git_header_handles_spaces_in_path() -> None:
    line = "diff --git a/my path/f.py b/my path/f.py"
    result = github_ci_self_heal._parse_diff_git_header(line)
    assert result == ("my path/f.py", "my path/f.py")


def test_changed_paths_from_diff_handles_spaces() -> None:
    diff = (
        "diff --git a/my path/f.py b/my path/f.py\n"
        "--- a/my path/f.py\n"
        "+++ b/my path/f.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    paths = github_ci_self_heal.changed_paths_from_diff(diff)
    assert paths == {"my path/f.py"}


def test_gate_cli_writes_json_out(tmp_path: Path) -> None:
    analysis_path = tmp_path / "analysis.json"
    metadata_path = tmp_path / "pr.json"
    diff_path = tmp_path / "pr.diff"
    output_path = tmp_path / "gate.json"
    analysis_path.write_text(json.dumps(asdict(_analysis())))
    metadata_path.write_text(json.dumps(_pr_metadata()))
    diff_path.write_text(_pr_diff())

    result = github_ci_self_heal.main(
        [
            "gate",
            str(analysis_path),
            str(metadata_path),
            str(diff_path),
            "--repository",
            "gptme/gptme",
            "--json-out",
            str(output_path),
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text())
    assert payload["eligible"] is True
    assert payload["patch_stats"]["paths"] == ["tests/test_example.py"]
