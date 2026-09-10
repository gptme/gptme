"""Policy-aware layered prompt loading, including legacy root compatibility."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.memory import MemoryRoot, MemoryStore
from gptme.memory.policy import POLICY_FILENAME
from gptme.prompts.workspace import prompt_workspace


def _entry(root: Path, name: str) -> None:
    (root / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: About {name}\ntype: feedback\n---\n"
        f"Remember {name}.\n",
        encoding="utf-8",
    )


def _policy(root: Path, selected: list[str], budget: int = 2048) -> None:
    (root / POLICY_FILENAME).write_text(
        json.dumps({"version": 1, "budget": budget, "selected": selected}),
        encoding="utf-8",
    )


def _memory_content(
    workspace: Path, roots: list[MemoryRoot], budget: int = 65536
) -> str:
    with (
        patch("gptme.prompts.workspace.resolve_roots", return_value=roots),
        patch("gptme.prompts.workspace.get_config") as config,
        patch("gptme.prompts.workspace.get_project_config", return_value=None),
        patch("gptme.prompts.workspace.get_tree_output", return_value=None),
        patch("gptme.prompts.workspace._get_git_status", return_value=None),
        patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
        patch("gptme.prompts.workspace._MEMORY_BUDGET_BYTES", budget),
    ):
        config.return_value.user = None
        messages = list(
            prompt_workspace(
                workspace=workspace,
                include_user_context=True,
                include_context_cmd=False,
            )
        )
    memories = [
        m.content for m in messages if m.content.startswith("## Persistent Memory")
    ]
    return memories[0].split("):\n\n", 1)[1] if memories else ""


def test_prompt_uses_stored_selection_without_hiding_recall(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    _entry(root, "selected")
    _entry(root, "recall-only")
    _policy(root, ["selected.md"])
    (root / "MEMORY.md").write_text("stale-index", encoding="utf-8")
    roots = [MemoryRoot("project", root)]

    content = _memory_content(tmp_path, roots)

    assert "selected.md" in content
    assert "recall-only" not in content
    assert "stale-index" not in content
    assert {entry.name for entry in MemoryStore(roots).entries()} == {
        "selected",
        "recall-only",
    }


@pytest.mark.parametrize(
    "policy_case",
    ["empty", "missing", "malformed", "overflow", "unreadable", "symlink", "dangling"],
)
def test_policy_never_falls_back_and_healthy_roots_survive(
    tmp_path: Path, policy_case: str
) -> None:
    before, target, after = [tmp_path / name for name in ("before", "target", "after")]
    for root in (before, target, after):
        root.mkdir()
    _entry(before, "healthy-before")
    _entry(after, "healthy-after")
    (target / "MEMORY.md").write_text("stale-index", encoding="utf-8")
    if policy_case == "empty":
        _policy(target, [])
    elif policy_case == "missing":
        _policy(target, ["missing.md"])
    elif policy_case == "malformed":
        (target / POLICY_FILENAME).write_text("{broken", encoding="utf-8")
    elif policy_case == "overflow":
        _entry(target, "selected")
        _policy(target, ["selected.md"], budget=1)
    elif policy_case == "unreadable":
        (target / POLICY_FILENAME).mkdir()
    else:
        _entry(target, "selected")
        policy_target = tmp_path / "outside-policy.json"
        if policy_case == "symlink":
            policy_target.write_text(
                json.dumps({"version": 1, "budget": 2048, "selected": ["selected.md"]}),
                encoding="utf-8",
            )
        (target / POLICY_FILENAME).symlink_to(policy_target)

    content = _memory_content(
        tmp_path,
        [
            MemoryRoot("project", before),
            MemoryRoot("cc", target),
            MemoryRoot("user", after),
        ],
    )

    assert "healthy-before.md" in content
    assert "healthy-after.md" in content
    assert "stale-index" not in content
    assert "selected.md" not in content


def test_policy_over_shared_budget_skips_only_that_root(tmp_path: Path) -> None:
    target, healthy = tmp_path / "target", tmp_path / "healthy"
    target.mkdir()
    healthy.mkdir()
    _entry(target, "too-big")
    _policy(target, ["too-big.md"])
    (target / "MEMORY.md").write_text("stale-index", encoding="utf-8")
    (healthy / "MEMORY.md").write_text("healthy", encoding="utf-8")

    content = _memory_content(
        tmp_path, [MemoryRoot("project", target), MemoryRoot("cc", healthy)], budget=10
    )

    assert content == "healthy"


def test_legacy_reads_and_separators_obey_byte_budget(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "MEMORY.md").write_text("12345", encoding="utf-8")
    (second / "MEMORY.md").write_text("é" * 100000, encoding="utf-8")
    # The fallback must not materialize the entire file before truncating it.
    with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
        content = _memory_content(
            tmp_path,
            [MemoryRoot("project", first), MemoryRoot("cc", second)],
            budget=10,
        )

    assert content == "12345\n\né"
    assert len(content.encode("utf-8")) <= 10


def test_legacy_read_charges_raw_bytes_after_incomplete_utf8(
    tmp_path: Path,
) -> None:
    first, second, third = [tmp_path / name for name in ("first", "second", "third")]
    for root in (first, second, third):
        root.mkdir()
    (first / "MEMORY.md").write_text("12345", encoding="utf-8")
    (second / "MEMORY.md").write_text("ééé", encoding="utf-8")
    (third / "MEMORY.md").write_text("must-not-fit", encoding="utf-8")

    content = _memory_content(
        tmp_path,
        [
            MemoryRoot("project", first),
            MemoryRoot("cc", second),
            MemoryRoot("user", third),
        ],
        budget=12,
    )

    assert content == "12345\n\néé"
    assert "must-not-fit" not in content
