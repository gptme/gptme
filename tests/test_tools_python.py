import os
import sys
from pathlib import Path
from typing import Literal, TypeAlias
from unittest.mock import patch

from gptme.tools.base import callable_signature
from gptme.tools.python import (
    _detect_venv,
    _get_venv_site_packages,
    _setup_venv_paths,
    execute_python,
)


def run(code):
    return next(execute_python(code, [], None)).content


def run_with_kwargs(code):
    return next(execute_python(None, None, {"code": code})).content


def test_execute_python():
    assert "2\n" in run("print(1 + 1)")
    assert "2\n" in run("a = 2\nprint(a)")
    assert "2\n" in run("a = 1\na += 1\nprint(a)")

    # test that vars are preserved between executions
    assert run("a = 2")
    assert "2\n" in run("print(a)")


def test_execute_python_with_kwargs():
    assert "2\n" in run_with_kwargs("print(1 + 1)")


TestType: TypeAlias = Literal["a", "b"]


def test_callable_signature():
    def f():
        pass

    assert callable_signature(f) == "f()"

    def g(a: int) -> str:
        return str(a)

    assert callable_signature(g) == "g(a: int) -> str"

    def h(a: TestType) -> str:
        return str(a)

    assert callable_signature(h) == 'h(a: Literal["a", "b"]) -> str'

    # Test generic types

    def i(a: list[int]) -> str:
        return str(a)

    assert callable_signature(i) == "i(a: list[int]) -> str"

    def j(a: list[int] | None) -> str:
        return str(a)

    assert callable_signature(j) == "j(a: Union[list[int], None]) -> str"

    def k(a: dict[str, int]) -> str:
        return str(a)

    assert callable_signature(k) == "k(a: dict[str, int]) -> str"

    # Test union types with | syntax
    def m(a: int | str) -> str:
        return str(a)

    assert callable_signature(m) == "m(a: Union[int, str]) -> str"


# Tests for venv detection (issue #29)


def test_detect_venv_from_env_var(tmp_path):
    """VIRTUAL_ENV env var should be detected."""
    venv_dir = tmp_path / "myvenv"
    venv_dir.mkdir()
    with patch.dict(os.environ, {"VIRTUAL_ENV": str(venv_dir)}):
        result = _detect_venv()
        assert result == venv_dir


def test_detect_venv_from_env_var_missing():
    """No VIRTUAL_ENV and no .venv in cwd should return None."""
    with patch.dict(os.environ, {}, clear=True):
        # Use a directory that definitely has no .venv
        with patch("gptme.tools.python.Path.cwd", return_value=Path("/nonexistent")):
            result = _detect_venv()
            assert result is None


def test_detect_venv_from_cwd(tmp_path):
    """Should detect .venv directory in cwd."""
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    with patch.dict(os.environ, {}, clear=True):
        with patch("gptme.tools.python.Path.cwd", return_value=tmp_path):
            result = _detect_venv()
            assert result == venv_dir


def test_detect_venv_env_var_takes_priority(tmp_path):
    """VIRTUAL_ENV should take priority over .venv in cwd."""
    env_venv = tmp_path / "env_venv"
    env_venv.mkdir()
    cwd_venv = tmp_path / ".venv"
    cwd_venv.mkdir()
    with patch.dict(os.environ, {"VIRTUAL_ENV": str(env_venv)}):
        with patch("gptme.tools.python.Path.cwd", return_value=tmp_path):
            result = _detect_venv()
            assert result == env_venv


def test_get_venv_site_packages(tmp_path):
    """Should find site-packages inside a venv."""
    sp = tmp_path / "lib" / "python3.11" / "site-packages"
    sp.mkdir(parents=True)
    result = _get_venv_site_packages(tmp_path)
    assert result == sp


def test_get_venv_site_packages_missing(tmp_path):
    """Should return None if no lib dir."""
    result = _get_venv_site_packages(tmp_path)
    assert result is None


def test_setup_venv_paths_adds_to_sys_path(tmp_path):
    """_setup_venv_paths should add venv site-packages to sys.path."""
    sp = tmp_path / "lib" / "python3.11" / "site-packages"
    sp.mkdir(parents=True)
    sp_str = str(sp)

    # Ensure it's not already in sys.path
    original_path = sys.path.copy()
    if sp_str in sys.path:
        sys.path.remove(sp_str)

    try:
        with patch.dict(os.environ, {"VIRTUAL_ENV": str(tmp_path)}):
            _setup_venv_paths()
            assert sp_str in sys.path
    finally:
        sys.path[:] = original_path


def test_setup_venv_paths_skips_own_venv():
    """Should not add gptme's own venv to sys.path."""
    original_path = sys.path.copy()
    try:
        with patch.dict(os.environ, {"VIRTUAL_ENV": sys.prefix}):
            _setup_venv_paths()
            # sys.path should be unchanged
            assert sys.path == original_path
    finally:
        sys.path[:] = original_path
