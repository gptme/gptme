"""Regression coverage for installed shell completions, not in-chat completion."""

import importlib.metadata
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.setup import _generate_click_completion, _setup_completions
from gptme.tui.main import main as tui


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_tui_model_completion(shell):
    env = {"_GPTME_TUI_COMPLETE": f"{shell}_complete"}
    if shell == "fish":
        env.update(COMP_WORDS="gptme-tui --model openrouter/", COMP_CWORD="openrouter/")
    else:
        env.update(COMP_WORDS="gptme-tui --model openrouter/", COMP_CWORD="2")
    result = CliRunner().invoke(tui, [], prog_name="gptme-tui", env=env)
    assert result.exit_code == 0, result.output
    assert "openrouter/anthropic/" in result.output
    assert "Conversation saved" not in result.output


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_setup_installs_entry_points_and_updates_legacy(tmp_path, monkeypatch, shell):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", f"/usr/bin/{shell}")
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    directories = {
        "bash": tmp_path / ".local/share/bash-completion/completions",
        "zsh": tmp_path / ".local/share/zsh/completions",
        "fish": tmp_path / ".config/fish/completions",
    }
    directory = directories[shell]
    directory.mkdir(parents=True)

    def filename(name: str) -> str:
        return (
            f"_{name}"
            if shell == "zsh"
            else name + (".fish" if shell == "fish" else "")
        )

    # Existing installs must be upgraded, including symlinks from old Fish setup.
    legacy = tmp_path / "legacy-completion"
    legacy.write_text("legacy gptme-only completion\n")
    (directory / filename("gptme")).symlink_to(legacy)
    _setup_completions()
    entry_points = importlib.metadata.distribution("gptme").entry_points
    for ep in entry_points:
        if ep.group != "console_scripts" or ep.name in {
            "gptme-acp",
            "gptme-eval-trends",
            "gptme-convert",
            "gptme-wut",
        }:
            continue
        content = (directory / filename(ep.name)).read_text()
        assert ep.name in content
        assert f"_{ep.name.upper().replace('-', '_')}_COMPLETE" in content
    assert legacy.read_text() == "legacy gptme-only completion\n"
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    _setup_completions()
    assert before == {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_generated_script_runs_in_shell(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    # Stub just the completion protocol; this tests shell registration/routing.
    command = tmp_path / "gptme-tui"
    response = (
        "plain\\nopenrouter/synthetic-model\\n_\\n"
        if shell == "zsh"
        else "plain,openrouter/synthetic-model\\n"
    )
    command.write_text(
        "#!/bin/sh\n"
        f'[ "$_GPTME_TUI_COMPLETE" = "{shell}_complete" ] || exit 9\n'
        f"printf '{response}'\n"
    )
    command.chmod(0o755)
    script = _generate_click_completion(shell, "gptme-tui")
    source = tmp_path / "completion"
    assert script is not None
    source.write_text(script)
    if shell == "zsh":
        # Stub the interactive display builtin, but use the real Zsh function
        # and protocol parsing. compadd normally requires an active ZLE widget.
        code = (
            f"compdef() {{ :; }}; source {source}; "
            "compadd() { print -l -- $completions; }; "
            "words=(gptme-tui --model openrouter/); CURRENT=3; "
            "_gptme_tui_completion"
        )
    elif shell == "fish":
        code = f"source {source}; complete -C 'gptme-tui --model openrouter/'"
    else:
        code = (
            f"source {source}; COMP_WORDS=(gptme-tui --model openrouter/); "
            "COMP_CWORD=2; _gptme_tui_completion gptme-tui; printf '%s\\n' \"${COMPREPLY[@]}\""
        )
    result = subprocess.run(
        [shell, "-c", code],
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "openrouter/synthetic-model" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize(
    ("entry_point", "args", "partial", "expected"),
    [
        ("gptme", ["--model"], "openrouter/", "openrouter/anthropic/"),
        ("gptme-server", ["serve", "--model"], "openrouter/", "openrouter/anthropic/"),
        ("gptme-util", [], "mod", "models"),
        ("gptme-util", ["models", "list"], "--pro", "--provider"),
        ("gptme-tui", [], "--inl", "--inline"),
        ("gptme-tui", ["--tool-format"], "x", "xml"),
    ],
)
def test_completion_protocol_for_cli_surfaces(
    shell, entry_point, args, partial, expected
):
    ep = next(
        ep
        for ep in importlib.metadata.distribution("gptme").entry_points
        if ep.group == "console_scripts" and ep.name == entry_point
    )
    env = {f"_{entry_point.upper().replace('-', '_')}_COMPLETE": f"{shell}_complete"}
    env["COMP_WORDS"] = " ".join([entry_point, *args, partial])
    env["COMP_CWORD"] = partial if shell == "fish" else str(len(args) + 1)
    result = CliRunner().invoke(ep.load(), [], prog_name=entry_point, env=env)
    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_custom_model_completion_is_offline(monkeypatch):
    from types import SimpleNamespace

    import click

    from gptme.cli.completion import complete_model

    monkeypatch.setattr(
        "gptme.config.get_config",
        lambda: SimpleNamespace(
            user=SimpleNamespace(
                providers=[SimpleNamespace(name="relay", default_model="custom-model")]
            )
        ),
    )

    def no_network(*args, **kwargs):
        pytest.fail("Shell completion must not fetch model lists")

    monkeypatch.setattr("gptme.llm.get_available_models", no_network)
    values = [
        item.value
        for item in complete_model(
            click.Context(tui), click.Option(["--model"]), "relay/"
        )
    ]
    assert values == ["relay/custom-model"]


def test_declining_install_leaves_files_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: False)
    _setup_completions()
    assert list(tmp_path.iterdir()) == []


def test_non_click_transports_have_no_generated_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", "/bin/fish")
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    _setup_completions()
    for name in ["gptme-acp", "gptme-convert", "gptme-eval-trends", "gptme-wut"]:
        assert not (tmp_path / ".config/fish/completions" / f"{name}.fish").exists()


def test_completion_installer_cli(tmp_path, monkeypatch):
    from gptme.cli.util import main

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = CliRunner().invoke(main, ["completions", "--shell", "fish"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".config/fish/completions/gptme-tui.fish").exists()


def test_completion_installer_unknown_shell(monkeypatch):
    from gptme.cli.util import main

    monkeypatch.setenv("SHELL", "/bin/unknown")
    result = CliRunner().invoke(main, ["completions"])
    assert result.exit_code == 1
    assert "pass --shell" in result.output


def test_custom_cli_types_keep_legacy_fish_value_completions(tmp_path, monkeypatch):
    import click

    from gptme.cli.main import CommaSeparatedChoice, ConversationName, WorkspacePath

    ctx = click.Context(tui)
    param = click.Option(["--value"])
    tools = CommaSeparatedChoice(["shell", "save"], allow_prefixes=["+", "-"])
    assert [item.value for item in tools.shell_complete(ctx, param, "shell,+sa")] == [
        "shell,+save"
    ]
    logs = tmp_path / "logs"
    (logs / "my-conversation").mkdir(parents=True)
    (logs / "my-conversation/conversation.jsonl").touch()
    monkeypatch.setattr("gptme.cli.main.get_logs_dir", lambda: logs)
    assert [
        item.value for item in ConversationName().shell_complete(ctx, param, "my-")
    ] == ["my-conversation"]
    assert WorkspacePath().shell_complete(ctx, param, "")[0].type == "dir"


def test_provider_suggestions_are_valid_model_values():
    import click

    from gptme.cli.completion import complete_model
    from gptme.cli.main import _validate_model_param

    ctx = click.Context(tui)
    param = click.Option(["--model"])
    candidates = complete_model(ctx, param, "openrouter")
    assert candidates
    for candidate in candidates:
        assert _validate_model_param(ctx, param, candidate.value) == candidate.value
