"""Tests for loading the user's own ``.env`` file."""

import os
import sys

import pytest

from gptme.util import env_file

MARKER = "GPTME_DOTENV_MARKER"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Would turn every load into a no-op and the negative tests into passes.
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv(MARKER, "")  # restored after the test
    monkeypatch.delenv(MARKER)
    return home


def _repo_with_dotenv(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(f"{MARKER}=repo\n")
    return repo


def _install_at(monkeypatch, package_root):
    module = package_root / "gptme" / "util" / "env_file.py"
    monkeypatch.setattr(env_file, "__file__", str(module))


@pytest.mark.parametrize("frozen", [True, False])
def test_working_directory_dotenv_is_not_loaded(tmp_path, monkeypatch, home, frozen):
    repo = _repo_with_dotenv(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    _install_at(monkeypatch, tmp_path / "install")

    env_file.load_user_dotenv()

    assert MARKER not in os.environ


def test_dotenv_of_the_project_holding_the_virtualenv_is_not_loaded(
    tmp_path, monkeypatch, home
):
    repo = _repo_with_dotenv(tmp_path)
    _install_at(monkeypatch, repo / ".venv" / "lib" / "python" / "site-packages")

    env_file.load_user_dotenv()

    assert MARKER not in os.environ


def test_source_checkout_dotenv_is_loaded(tmp_path, monkeypatch, home):
    checkout = _repo_with_dotenv(tmp_path)
    _install_at(monkeypatch, checkout)

    env_file.load_user_dotenv()

    assert os.environ[MARKER] == "repo"


@pytest.mark.parametrize("frozen", [True, False])
def test_home_dotenv_is_loaded(monkeypatch, home, frozen):
    (home / ".env").write_text(f"{MARKER}=home\n")
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    _install_at(monkeypatch, home / "tools" / "site-packages")

    env_file.load_user_dotenv()

    assert os.environ[MARKER] == "home"
