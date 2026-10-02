import os
import sys
from pathlib import Path
from typing import Literal, TypeAlias
from unittest.mock import patch

import pytest

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


def test_init_describes_docker_execution_contract():
    with patch.dict(os.environ, {"GPTME_SANDBOX": "docker"}):
        from gptme.tools.python import init

        docker_tool = init()
    assert "fresh Docker\ncontainer" in docker_tool.instructions
    assert "no prior state" in docker_tool.instructions
    assert "registered host\nfunctions" in docker_tool.instructions
    assert "Available functions:" not in docker_tool.instructions_format["markdown"]


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


def test_execute_python_returns_list_of_messages():
    """execute_python yields each Message when a list[Message] is returned.

    This is the key integration needed for observe_web() which returns
    list[Message] (ARIA snapshot + optional screenshot). Without this fix,
    observe_web() results would be shown as repr(list) instead of each
    message being delivered to the conversation.
    """

    code = """\
from gptme.message import Message
[Message("system", "msg1"), Message("system", "msg2")]
"""
    msgs = list(execute_python(code, [], None))
    # Both messages should be yielded, not collapsed into a repr string
    assert len(msgs) == 2, f"Expected 2 messages, got {len(msgs)}: {msgs}"
    assert msgs[0].content == "msg1"
    assert msgs[1].content == "msg2"


def test_execute_python_printed_panel_json_is_not_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GPTME_SANDBOX", "none")
    messages = list(
        execute_python(
            'print(\'{"panel_hints": [{"id": "preview", "kind": "live_app"}]}\')',
            [],
            None,
        )
    )
    assert len(messages) == 1
    assert "panel_hints" in messages[0].content
    assert not (messages[0].metadata or {}).get("panel_hints")


def test_execute_python_returns_empty_list_falls_through_to_repr():
    """Empty list should fall through to the normal repr path, showing 'Result: []'.

    An empty list from any computation — whether from observe_web returning no
    results or a regular list comprehension filtering to nothing — should show
    the LLM a clear 'Result: []' rather than silent no-output (which hides
    information) or a potentially misleading 'No results returned.' message.

    The list[Message] fast-path only activates for non-empty lists where every
    item is a Message; empty lists fall through to the normal repr output.
    """
    code = """\
from gptme.message import Message
[m for m in [] if isinstance(m, Message)]  # evaluates to []
"""
    msgs = list(execute_python(code, [], None))
    assert len(msgs) == 1, f"Expected 1 repr message for empty list, got: {msgs}"
    assert "[]" in msgs[0].content, (
        f"Expected '[]' in repr output, got: {msgs[0].content!r}"
    )


def test_execute_python_returns_mixed_list_falls_through():
    """A mixed list (Message + non-Message) should NOT yield from; show as repr.

    A type guard checking only result[0] would incorrectly yield the non-Message
    items, violating the generator's return type. The all() check prevents this.
    """
    code = """\
from gptme.message import Message
[Message("system", "real"), "not-a-message"]
"""
    msgs = list(execute_python(code, [], None))
    # Should fall through to repr output, not yield 2 items of mixed type
    assert len(msgs) == 1, f"Expected 1 repr output message, got {len(msgs)}: {msgs}"
    assert "not-a-message" in msgs[0].content


def test_cap_output_large_stdout():
    """Test that huge stdout is truncated with head+tail shown."""
    from gptme.tools.python import _cap_output

    # Create output larger than the cap (10 MiB default)
    huge_output = "x" * (15 * 1024 * 1024)  # 15 MiB
    capped = _cap_output(huge_output)

    # Should be smaller than original (about 50% due to head+tail)
    assert len(capped) < len(huge_output)
    assert "omitted" in capped
    assert capped.startswith("xxx")  # head preserved
    assert capped.endswith("xxx")  # tail preserved


def test_cap_output_small_output():
    """Test that small output is not truncated."""
    from gptme.tools.python import _cap_output

    small = "short output\n"
    assert _cap_output(small) == small


def test_cap_output_multibyte_chars():
    """Byte cap must count bytes, not characters — 5M four-byte chars = ~20 MiB."""
    from gptme.tools.python import _DEFAULT_MAX_OUTPUT_BYTES, _cap_output

    # Each '𝄞' is 4 UTF-8 bytes; 3M of them ≈ 12 MiB, well above the 10 MiB cap.
    huge_multibyte = "𝄞" * (3 * 1024 * 1024)
    assert len(huge_multibyte.encode("utf-8")) > _DEFAULT_MAX_OUTPUT_BYTES
    capped = _cap_output(huge_multibyte)
    assert len(capped.encode("utf-8")) < len(huge_multibyte.encode("utf-8"))
    assert "omitted" in capped


def test_teeio_truncation_marker():
    """TeeIO must add an omission marker when it silently drops output past the cap."""
    import io

    from gptme.tools.python import _DEFAULT_MAX_OUTPUT_BYTES, TeeIO, _cap_output

    # Write more than the cap to trigger truncation
    tee = TeeIO(io.StringIO())  # discard the "original stream" output
    chunk = "a" * 1024  # 1 KiB per write
    total_written = 0
    while total_written <= _DEFAULT_MAX_OUTPUT_BYTES + len(chunk):
        tee.write(chunk)
        total_written += len(chunk)

    assert tee._truncated, "TeeIO should have set _truncated after exceeding cap"
    captured = tee.get_captured()
    assert "omitted" in captured, "get_captured() must include a truncation marker"
    assert "bytes omitted" in captured
    assert len(captured.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert _cap_output(captured) == captured, (
        "marker must not trigger a second truncation"
    )


def test_teeio_single_oversized_write_keeps_head():
    """One write crossing the cap must keep the part that fits, not drop it all."""
    import io

    from gptme.tools.python import _DEFAULT_MAX_OUTPUT_BYTES, TeeIO, _cap_output

    tee = TeeIO(io.StringIO())
    tee.write("x" * (15 * 1024 * 1024))

    captured = tee.get_captured()
    assert captured.startswith("xxx"), "head of a crossing write must be retained"
    assert "bytes omitted" in captured
    assert len(captured.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert _cap_output(captured) == captured


def test_cap_output_and_teeio_accept_lone_surrogates():
    """Lone surrogates (e.g. surrogateescape filenames) must not raise."""
    import io

    from gptme.tools.python import TeeIO, _cap_output

    s = "a" + chr(0xDCFF) + "b"
    assert _cap_output(s) == s
    tee = TeeIO(io.StringIO())
    tee.write(s)
    assert tee.get_captured() == s


def test_cap_output_result_within_cap():
    """Truncated output (marker included) must not exceed the byte cap."""
    from gptme.tools.python import _DEFAULT_MAX_OUTPUT_BYTES, _cap_output

    for s in ("x" * (_DEFAULT_MAX_OUTPUT_BYTES + 1), chr(0xDCFF) * (4 * 1024 * 1024)):
        capped = _cap_output(s)
        assert len(capped.encode("utf-8", errors="surrogatepass")) <= (
            _DEFAULT_MAX_OUTPUT_BYTES
        )


def test_teeio_write_reports_full_length_when_truncating():
    import io

    from gptme.tools.python import TeeIO

    tee = TeeIO(io.StringIO())
    data = "x" * (15 * 1024 * 1024)
    assert tee.write(data) == len(data)
