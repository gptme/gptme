"""Keep throttled GitHub pages out of linkcheck without hiding pinned refs."""

import ast
import re
from pathlib import Path

import pytest


def linkcheck_patterns(source: str) -> list[re.Pattern[str]]:
    """Read the real Sphinx setting without importing optional docs extensions."""
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "linkcheck_ignore"
            for target in node.targets
        )
    )
    return [re.compile(pattern) for pattern in ast.literal_eval(assignment.value)]


@pytest.fixture
def patterns() -> list[re.Pattern[str]]:
    conf = Path(__file__).resolve().parents[1] / "docs" / "conf.py"
    return linkcheck_patterns(conf.read_text())


@pytest.mark.parametrize(
    "page", ["blob/main/providers/anthropic.yaml", "tree/main/providers"]
)
@pytest.mark.parametrize(
    "repo", ["ai-openshell/openshell", "NVIDIA/OpenShell", "other/project"]
)
def test_third_party_file_pages_are_ignored(
    patterns: list[re.Pattern[str]], repo: str, page: str
) -> None:
    url = f"https://github.com/{repo}/{page}"
    assert any(pattern.match(url) for pattern in patterns)


@pytest.mark.parametrize("owner", ["gptme", "GPTME", "GpTmE"])
@pytest.mark.parametrize("ref", ["main", "v0.35.0", "d3cfb567309b"])
@pytest.mark.parametrize("kind", ["blob", "tree"])
def test_gptme_pinned_refs_remain_checked(
    patterns: list[re.Pattern[str]], owner: str, ref: str, kind: str
) -> None:
    url = f"https://github.com/{owner}/gptme/{kind}/{ref}/docs"
    assert not any(pattern.match(url) for pattern in patterns)


@pytest.mark.parametrize("repo", ["gptme/gptme", "ai-openshell/openshell"])
@pytest.mark.parametrize("suffix", ["", "/", "/issues/1", "/pull/1", "/discussions/1"])
def test_repository_and_conversation_pages_remain_checked(
    patterns: list[re.Pattern[str]], repo: str, suffix: str
) -> None:
    url = f"https://github.com/{repo}{suffix}"
    assert not any(pattern.match(url) for pattern in patterns)


@pytest.mark.parametrize("kind", ["blob", "tree"])
def test_gptme_master_pages_keep_git_based_validation(
    patterns: list[re.Pattern[str]], kind: str
) -> None:
    url = f"https://github.com/gptme/gptme/{kind}/master/docs"
    assert any(pattern.match(url) for pattern in patterns)
