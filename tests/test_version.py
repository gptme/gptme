"""Tests for version computation in gptme.__version__."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

v = importlib.import_module("gptme.__version__")


def _dist(direct_url: dict | None):
    text = json.dumps(direct_url) if direct_url is not None else None
    return SimpleNamespace(read_text=lambda _name: text)


def _compute(direct_url: dict | None, git_version: str | None):
    with (
        patch.object(v.importlib.metadata, "version", return_value="0.35.1"),
        patch.object(
            v.importlib.metadata, "distribution", return_value=_dist(direct_url)
        ),
        patch.object(v, "get_git_version", return_value=git_version) as mock_git,
    ):
        return v._compute_version(), mock_git


def test_non_editable_vcs_install_ignores_enclosing_repo_dirty():
    """A non-editable VCS install must not inherit .dirty from a surrounding repo (#4874)."""
    version, mock_git = _compute(
        {
            "url": "https://github.com/gptme/gptme",
            "vcs_info": {"vcs": "git", "commit_id": "12345678abcdef"},
        },
        git_version="0.1.0+deadbeef.dirty",
    )
    assert version == "0.35.1+12345678"
    mock_git.assert_not_called()


def test_editable_install_keeps_own_dirty_indicator():
    version, mock_git = _compute(
        {"url": "file:///src/gptme", "dir_info": {"editable": True}},
        git_version="0.35.1+abcdef12.dirty",
    )
    assert version == "0.35.1+abcdef12.dirty"
    mock_git.assert_called_once()


def test_editable_install_without_git_falls_back_to_unknown():
    version, _ = _compute(
        {"url": "file:///src/gptme", "dir_info": {"editable": True}},
        git_version=None,
    )
    assert version == "0.35.1+unknown"


def test_plain_install_does_not_probe_git():
    """A regular (index/wheel) install has no direct_url.json and must not run git."""
    version, mock_git = _compute(None, git_version="0.1.0+deadbeef.dirty")
    assert version == "0.35.1"
    mock_git.assert_not_called()
