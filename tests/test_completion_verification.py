"""Tests for opt-in completion-time test discovery."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from gptme.tools.base import ToolUse
from gptme.util.completion_verification import (
    VerificationCommand,
    approved_execution_argv,
    classify_authoring_tool_use,
    discover_verification_command,
    npm_lifecycle_script,
    restore_approved_snapshots,
    uses_approved_snapshot,
)


def _tool(
    name: str,
    *,
    content: str | None = None,
    args: list[str] | None = None,
    kwargs: dict[str, str] | None = None,
) -> ToolUse:
    return ToolUse(tool=name, args=args, content=content, kwargs=kwargs)


@pytest.mark.parametrize("tool_name", ["save", "append", "patch", "morph"])
def test_first_class_code_writes_arm_discovery(tool_name: str) -> None:
    assert classify_authoring_tool_use(
        _tool(tool_name, args=["src/example.py"], kwargs={"path": "src/example.py"})
    )


@pytest.mark.parametrize("path", ["README.md", "docs/guide.rst", "docs/page.mdx"])
def test_known_documentation_only_writes_do_not_arm(path: str) -> None:
    assert not classify_authoring_tool_use(
        _tool("save", args=[path], kwargs={"path": path})
    )


def test_multi_file_documentation_patch_does_not_arm() -> None:
    assert not classify_authoring_tool_use(
        _tool(
            "patch",
            content=(
                "=== PATH: README.md ===\n"
                "<<<<<<< ORIGINAL\nold\n=======\nnew\n>>>>>>> UPDATED\n"
                "=== PATH: docs/guide.rst ===\n"
                "<<<<<<< ORIGINAL\nold\n=======\nnew\n>>>>>>> UPDATED"
            ),
        )
    )


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "uv run pytest tests/ -x",
        "tox",
        "npm test",
        "cargo test",
        "make test",
        "ruff check .",
        "mypy gptme",
    ],
)
def test_test_and_build_commands_do_not_count_as_authoring(command: str) -> None:
    assert not classify_authoring_tool_use(_tool("shell", content=command))


@pytest.mark.parametrize(
    "tool_use",
    [
        _tool("shell", content="cat > src/example.py <<'EOF'\nvalue = 1\nEOF"),
        _tool("shell", kwargs={"command": "sed -i 's/a/b/' src/example.py"}),
        _tool("tmux", kwargs={"command": "send-keys 'python generate.py' Enter"}),
        _tool("ipython", kwargs={"code": "Path('generated.py').write_text('x')"}),
    ],
)
def test_opaque_mutating_shell_family_calls_arm_discovery(tool_use: ToolUse) -> None:
    assert classify_authoring_tool_use(tool_use)


@pytest.mark.parametrize(
    "command",
    ["git status --short", "rg -n TODO src", "cat pyproject.toml", "ls -la"],
)
def test_read_only_shell_calls_do_not_arm_discovery(command: str) -> None:
    assert not classify_authoring_tool_use(_tool("shell", content=command))


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q && sed -i 's/a/b/' src/example.py",
        "git diff && echo x > src/file.py",
        "ls && python generate.py",
        "pytest -q | tee src/generated.py",
    ],
)
def test_compound_commands_with_writes_arm_discovery(command: str) -> None:
    assert classify_authoring_tool_use(_tool("shell", content=command))


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q && echo done",
        "ls && git status",
        "pytest -q || tox",
    ],
)
def test_compound_commands_without_writes_do_not_arm(command: str) -> None:
    assert not classify_authoring_tool_use(_tool("shell", content=command))


@pytest.mark.parametrize(
    "command",
    [
        "echo ok # note && rm old-file",
        "echo 'foo && rm old-file'",
        'echo "foo && rm old-file"',
        "cat <<'EOF'\nplease rm old-file\nEOF",
    ],
)
def test_quoted_comment_and_heredoc_writes_do_not_arm(command: str) -> None:
    assert not classify_authoring_tool_use(_tool("shell", content=command))


def _assert_fingerprint(command: VerificationCommand, source: Path) -> None:
    assert command.source_fingerprints == (
        (source, hashlib.sha256(source.read_bytes()).hexdigest()),
    )


def test_discovers_uv_pytest_from_explicit_pytest_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    monkeypatch.setattr(
        "gptme.util.completion_verification.shutil.which",
        lambda cmd: "/bin/uv" if cmd == "uv" else None,
    )

    command = discover_verification_command(tmp_path)

    assert command is not None
    assert command.argv == ("uv", "run", "pytest", "-x", "-q")
    assert command.display == "uv run pytest -x -q"
    assert "pytest.ini" in command.reason
    _assert_fingerprint(command, config)


def test_runner_precedence_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = 'demo'\nversion = '0.1.0'\n"
    )
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    monkeypatch.setattr(
        "gptme.util.completion_verification.shutil.which", lambda _cmd: None
    )

    command = discover_verification_command(tmp_path)

    assert command is not None
    assert command.argv == ("pytest", "-x", "-q")


def test_discovers_tox_cargo_and_make(tmp_path: Path) -> None:
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py\n")
    assert discover_verification_command(tmp_path).argv == ("tox",)  # type: ignore[union-attr]

    (tmp_path / "tox.ini").unlink()
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = 'demo'\nversion = '0.1.0'\n"
    )
    assert discover_verification_command(tmp_path).argv == ("cargo", "test")  # type: ignore[union-attr]

    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    assert discover_verification_command(tmp_path).argv == ("make", "test")  # type: ignore[union-attr]


def test_npm_requires_exact_test_script_and_previews_lifecycle(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        json.dumps(
            {
                "scripts": {
                    "pretest": "node prepare.js",
                    "test": "vitest run",
                    "posttest": "node cleanup.js",
                    "test:unit": "vitest",
                }
            }
        )
    )

    command = discover_verification_command(tmp_path)

    assert command is not None
    assert command.argv == ("npm", "test")
    assert command.preview is not None
    assert "pretest: node prepare.js" in command.preview
    assert "test: vitest run" in command.preview
    assert "posttest: node cleanup.js" in command.preview
    assert "npx" not in command.display
    _assert_fingerprint(command, package)


def test_nonstandard_javascript_script_does_not_discover_runner(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:unit": "vitest run"}})
    )

    assert discover_verification_command(tmp_path) is None


def test_no_supported_runner_returns_none(tmp_path: Path) -> None:
    assert discover_verification_command(tmp_path) is None


def test_bare_pyproject_does_not_imply_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")

    assert discover_verification_command(tmp_path) is None


def test_comment_only_pytest_mention_does_not_discover(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n# [tool.pytest.ini_options]\n"
    )

    assert discover_verification_command(tmp_path) is None


def test_string_pytest_mention_does_not_discover(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\ndescription = '[tool.pytest] is mentioned'\n"
    )

    assert discover_verification_command(tmp_path) is None


def test_unreadable_makefile_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("test:\n\t@true\n")
    original = Path.read_text

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "Makefile":
            raise OSError("unreadable")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", boom)
    assert discover_verification_command(tmp_path) is None


def test_approved_make_argv_binds_snapshot(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("test:\n\t@true\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    assert uses_approved_snapshot(command)
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    argv, _bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    assert argv[:2] == ("make", "-f")
    assert argv[-1] == "test"
    assert Path(argv[2]).read_bytes() == b"test:\n\t@true\n"


def test_npm_lifecycle_script_uses_approved_bodies() -> None:
    blob = json.dumps(
        {
            "name": "demo",
            "scripts": {"pretest": "node prep.js", "test": "vitest run"},
        }
    ).encode()
    script = npm_lifecycle_script(blob)
    assert script is not None
    assert "node prep.js" in script
    assert "vitest run" in script
    assert "npm test" not in script
    assert "INIT_CWD" in script
    assert "npm_lifecycle_event" in script
    assert "npm_package_name" in script
    assert script.count("sh -c") == 2
    pretest_at = script.index("npm_lifecycle_event='pretest'")
    test_at = script.index("npm_lifecycle_event='test'")
    assert pretest_at < test_at


def test_approved_npm_argv_invokes_shell_wrapper(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"name": "demo", "scripts": {"test": "vitest run"}}))
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    argv, _bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    wrapper = Path(argv[-1])
    assert wrapper.exists()
    if os.name == "nt":
        assert "cmd" in Path(argv[0]).name.lower()
        assert "vitest run" in wrapper.read_text()
    else:
        assert argv[0].endswith("sh") or argv[0].endswith("sh.exe")
        body = wrapper.read_text()
        assert "sh -c" in body
        assert "vitest run" in body


def test_approved_pytest_pyproject_binds_ini_snapshot(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.pytest.ini_options]\naddopts = '-q'\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    assert uses_approved_snapshot(command)
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    argv, _bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    assert "-c" in argv
    assert "--rootdir" in argv
    ini = Path(argv[argv.index("-c") + 1])
    assert ini.read_text() == "[pytest]\naddopts = -q\n"


def test_approved_tox_argv_binds_workspace_side_file(tmp_path: Path) -> None:
    tox_ini = tmp_path / "tox.ini"
    tox_ini.write_text("[tox]\nenvlist = py\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    argv, bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    assert argv[:2] == ("tox", "-c")
    bound = Path(argv[2])
    assert bound.read_bytes() == b"[tox]\nenvlist = py\n"
    tox_ini.write_text("[tox]\nenvlist = evil\n")
    assert bound.read_bytes() == b"[tox]\nenvlist = py\n"
    restore_approved_snapshots(bind)
    assert not bound.exists()


def test_approved_cargo_swaps_manifest_then_restores(tmp_path: Path) -> None:
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    cargo.write_text("[package]\nname = 'evil'\nversion = '0.1.0'\n")
    argv, bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    assert argv == ("cargo", "test")
    assert b"name = 'demo'" in cargo.read_bytes()
    restore_approved_snapshots(bind)
    assert b"name = 'evil'" in cargo.read_bytes()


def test_cargo_restore_leaves_intervening_edits(tmp_path: Path) -> None:
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    _argv, bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    cargo.write_text("[package]\nname = 'concurrent'\nversion = '0.1.0'\n")
    restore_approved_snapshots(bind)
    assert b"name = 'concurrent'" in cargo.read_bytes()


def test_cargo_restore_does_not_recreate_deleted_manifest(tmp_path: Path) -> None:
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    _argv, bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    cargo.unlink()
    restore_approved_snapshots(bind)
    assert not cargo.exists()


def test_cleanup_ignores_child_rewritten_bound_paths(tmp_path: Path) -> None:
    tox_ini = tmp_path / "tox.ini"
    tox_ini.write_text("[tox]\nenvlist = py\n")
    victim = tmp_path / "must-survive"
    victim.write_text("keep\n")
    command = discover_verification_command(tmp_path)
    assert command is not None
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    argv, bind = approved_execution_argv(command, snapshot_dir, tmp_path)
    (snapshot_dir / "BOUND_PATHS").write_text(str(victim) + "\n")
    restore_approved_snapshots(bind)
    assert victim.exists()
    assert not Path(argv[2]).exists()
