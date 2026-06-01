import os
import sys
from pathlib import Path
from typing import Literal, TypeAlias
from unittest.mock import patch

from gptme.tools.base import callable_signature
from gptme.tools.python import (
    _detect_venv,
    _get_venv_site_packages,
    _make_plot_artifacts,
    _setup_venv_paths,
    _snapshot_images,
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


def test_execute_python_stdout_with_result():
    """When a cell prints AND returns a value, both should appear in output."""
    output = run('print("hello")\n42')
    assert "hello" in output, "stdout should be shown when result also exists"
    assert "42" in output, "result should be shown"


def test_execute_python_result_only():
    """When a cell only returns a value (no print), result should appear."""
    output = run("42")
    assert "42" in output


def test_execute_python_stdout_only():
    """When a cell only prints (no return value), stdout should appear."""
    output = run('print("hello")')
    assert "hello" in output


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
    # Use a directory that definitely has no .venv
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("gptme.tools.python.Path.cwd", return_value=Path("/nonexistent")),
    ):
        result = _detect_venv()
        assert result is None


def test_detect_venv_from_cwd(tmp_path):
    """Should detect .venv directory in cwd."""
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("gptme.tools.python.Path.cwd", return_value=tmp_path),
    ):
        result = _detect_venv()
        assert result == venv_dir


def test_detect_venv_env_var_takes_priority(tmp_path):
    """VIRTUAL_ENV should take priority over .venv in cwd."""
    env_venv = tmp_path / "env_venv"
    env_venv.mkdir()
    cwd_venv = tmp_path / ".venv"
    cwd_venv.mkdir()
    with (
        patch.dict(os.environ, {"VIRTUAL_ENV": str(env_venv)}),
        patch("gptme.tools.python.Path.cwd", return_value=tmp_path),
    ):
        result = _detect_venv()
        assert result == env_venv


def test_get_venv_site_packages(tmp_path):
    """Should find site-packages inside a venv."""
    sp = tmp_path / "lib" / "python3.11" / "site-packages"
    sp.mkdir(parents=True)
    result = _get_venv_site_packages(tmp_path)
    assert result == sp


def test_get_venv_site_packages_windows(tmp_path):
    """Should find Windows-style Lib/site-packages."""
    sp = tmp_path / "Lib" / "site-packages"
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


# --- Plot artifact descriptor tests ---


def test_snapshot_images_empty(tmp_path):
    """Empty directory → empty snapshot."""
    assert _snapshot_images(tmp_path) == {}


def test_snapshot_images_captures_images(tmp_path):
    """Images in directory are captured with their mtimes."""
    (tmp_path / "plot.png").write_bytes(b"")
    (tmp_path / "chart.svg").write_bytes(b"")
    snap = _snapshot_images(tmp_path)
    assert tmp_path / "plot.png" in snap
    assert tmp_path / "chart.svg" in snap


def test_snapshot_images_ignores_non_images(tmp_path):
    """Non-image files are not captured."""
    (tmp_path / "data.csv").write_text("a,b")
    (tmp_path / "script.py").write_text("pass")
    assert _snapshot_images(tmp_path) == {}


def test_make_plot_artifacts_new_file(tmp_path):
    """A file present in *after* but not *before* produces a descriptor."""
    path = tmp_path / "plot.png"
    path.write_bytes(b"")
    before: dict[Path, float] = {}
    after = {path: path.stat().st_mtime}
    artifacts = _make_plot_artifacts(before, after)
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "image"
    assert artifacts[0]["tool"] == "python"
    assert artifacts[0]["source_type"] == "attachment"
    assert artifacts[0]["path"] == str(path)


def test_make_plot_artifacts_modified_file(tmp_path):
    """A file whose mtime changed produces a descriptor."""
    path = tmp_path / "plot.png"
    path.write_bytes(b"v1")
    before = {path: 1000.0}
    after = {path: 2000.0}
    artifacts = _make_plot_artifacts(before, after)
    assert len(artifacts) == 1


def test_make_plot_artifacts_unchanged_file(tmp_path):
    """A file with the same mtime in before and after → no descriptor."""
    path = tmp_path / "old.png"
    path.write_bytes(b"")
    mtime = path.stat().st_mtime
    snap = {path: mtime}
    assert _make_plot_artifacts(snap, snap) == []


def test_make_plot_artifacts_mime_types(tmp_path):
    """Correct MIME type is assigned per extension."""
    cases = {
        "a.png": "image/png",
        "b.svg": "image/svg+xml",
        "c.jpg": "image/jpeg",
        "d.jpeg": "image/jpeg",
        "e.gif": "image/gif",
        "f.pdf": "application/pdf",
    }
    before: dict[Path, float] = {}
    after: dict[Path, float] = {}
    for name in cases:
        p = tmp_path / name
        p.write_bytes(b"")
        after[p] = p.stat().st_mtime
    artifacts = _make_plot_artifacts(before, after)
    by_path = {Path(a["path"]).name: a["mime_type"] for a in artifacts}
    for name, expected_mime in cases.items():
        assert by_path[name] == expected_mime, (
            f"{name}: {by_path[name]} != {expected_mime}"
        )


def test_execute_python_plot_artifact(tmp_path):
    """execute_python attaches an artifact descriptor when code creates an image."""
    plot_file = tmp_path / "plot.png"

    # Patch Path.cwd() to return tmp_path so the pre/post snapshot sees the file
    with patch("gptme.tools.python.Path.cwd", return_value=tmp_path):
        # Write the plot file *during* execution by creating it inside the code
        code = f"open('{plot_file}', 'wb').write(b'fake png')"
        msg = next(execute_python(code, [], None))

    assert msg.metadata is not None
    artifacts = msg.metadata.get("artifacts", [])
    assert any(a["tool"] == "python" and a["kind"] == "image" for a in artifacts), (
        f"Expected image artifact, got: {artifacts}"
    )


def test_execute_python_no_plot_no_artifact(tmp_path):
    """execute_python does NOT add artifacts when no image files are created."""
    with patch("gptme.tools.python.Path.cwd", return_value=tmp_path):
        msg = next(execute_python("x = 1 + 1", [], None))

    artifacts = (msg.metadata or {}).get("artifacts", [])
    assert artifacts == []
