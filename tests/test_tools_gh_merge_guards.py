"""Offline native merge argument contracts; no irreversible helper is executed."""

import shlex
from unittest.mock import patch

import pytest

from gptme.message import Message
from gptme.tools.gh import execute_gh

SHA = "a" * 40


def invoke(command: str, structured: bool, url: str | None = None) -> list[Message]:
    kwargs = {"url": url} if url else {}
    if structured:
        kwargs["command"] = command
        return list(execute_gh(None, None, kwargs))
    return list(execute_gh(None, shlex.split(command), kwargs or None))


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "suffix",
    [
        f"--match-head-commmit {SHA}",
        "--admin",
        "--auto=false",
        "unexpected",
        "--match-head-commit",
        "--match-head-commit=",
        "--match-head-commit ''",
        "--match-head-commit --auto",
        "--match-head-commit=--auto",
        f"--match-head-commit {SHA} --match-head-commit {SHA}",
        f"--match-head-commit={SHA} --match-head-commit={SHA}",
        f"--match-head-commit={SHA} --match-head-commit {SHA}",
        "--squash --rebase",
        "--merge --squash",
        "--rebase --merge",
    ],
)
def test_invalid_merge_options_fail_closed(suffix: str, structured: bool) -> None:
    with (
        patch("gptme.tools.gh.merge_github_pr") as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("transport")),
    ):
        messages = invoke(f"pr merge owner/repo#42 {suffix}", structured)
    merge.assert_not_called()
    assert len(messages) == 1
    assert messages[0].content.startswith("Error:")


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "guard", [f"--match-head-commit {SHA}", f"--match-head-commit={SHA}"]
)
@pytest.mark.parametrize("method", ["squash", "rebase", "merge"])
@pytest.mark.parametrize(
    "ref",
    ["owner/repo#42", "https://github.com/owner/repo/pull/42", "42 -R owner/repo"],
)
def test_valid_merge_options_preserved(
    guard: str, method: str, ref: str, structured: bool
) -> None:
    with (
        patch(
            "gptme.tools.gh.merge_github_pr",
            return_value={"success": True, "message": "synthetic", "sha": "merged"},
        ) as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("transport")),
    ):
        messages = invoke(
            f"pr merge {ref} --{method} --auto --delete-branch {guard}", structured
        )
    merge.assert_called_once_with(
        "owner",
        "repo",
        "42",
        method=method,
        auto=True,
        delete_branch=True,
        match_head_commit=SHA,
    )
    assert messages[0].content == "synthetic\nMerge commit: merged"


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "suffix", ["--admin", "--squash --rebase", "--match-head-commit --auto"]
)
def test_url_kwarg_does_not_skip_first_option(suffix: str, structured: bool) -> None:
    with patch("gptme.tools.gh.merge_github_pr") as merge:
        messages = invoke(
            f"pr merge {suffix}", structured, "https://github.com/owner/repo/pull/42"
        )
    merge.assert_not_called()
    assert messages[0].content.startswith("Error:")


@pytest.mark.parametrize("structured", [False, True])
def test_url_kwarg_preserves_guard_and_default_method(structured: bool) -> None:
    with patch(
        "gptme.tools.gh.merge_github_pr",
        return_value={"success": True, "message": "synthetic"},
    ) as merge:
        messages = invoke(
            f"pr merge --match-head-commit={SHA}",
            structured,
            "https://github.com/owner/repo/pull/42",
        )
    merge.assert_called_once_with(
        "owner",
        "repo",
        "42",
        method="squash",
        auto=False,
        delete_branch=False,
        match_head_commit=SHA,
    )
    assert messages[0].content == "synthetic"


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "ref",
    [
        "42 --repo owner/repo",
        "--repo owner/repo #42",
        "42 --repo=owner/repo",
        "42 -Rowner/repo",
        "42 -R=owner/repo",
        "owner/repo#42 --repo other/repo",
    ],
)
def test_repository_selectors_preserve_default_merge(
    ref: str, structured: bool
) -> None:
    with (
        patch(
            "gptme.tools.gh.merge_github_pr",
            return_value={"success": False, "message": "synthetic refusal"},
        ) as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("transport")),
    ):
        messages = invoke(f"pr merge {ref}", structured)
    merge.assert_called_once_with(
        "owner",
        "repo",
        "42",
        method="squash",
        auto=False,
        delete_branch=False,
        match_head_commit=None,
    )
    assert messages[0].content == "Error: synthetic refusal"


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("suffix", ["--repo", "--repo=", "-R --auto", "--repo noslash"])
def test_invalid_repository_never_merges(suffix: str, structured: bool) -> None:
    with (
        patch("gptme.tools.gh.merge_github_pr") as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("transport")),
    ):
        messages = invoke(f"pr merge owner/repo#42 {suffix}", structured)
    merge.assert_not_called()
    assert messages[0].content.startswith("Error:")
