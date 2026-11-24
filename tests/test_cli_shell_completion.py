"""Regression coverage for installed shell completions, not in-chat completion."""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.argparse_completion import (
    ARGPARSE_PARSERS,
    generate_argparse_completion,
    load_parser,
)
from gptme.cli.setup import _generate_click_completion, _setup_completions
from gptme.tui.main import main as tui


@pytest.fixture(autouse=True)
def isolate_completion_install_paths(monkeypatch):
    # Home-only mocks must not leave a developer's active XDG paths writable.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


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
        if ep.group != "console_scripts" or ep.name == "gptme-acp":
            continue
        content = (directory / filename(ep.name)).read_text()
        assert ep.name in content
        if ep.name in ARGPARSE_PARSERS:
            assert f"Static: completing never runs {ep.name}." in content
        else:
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
        else "plain,openrouter/synthetic-model\\nplain,shell,browser\\n"
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
        code = f"source {source}; complete -C 'gptme-tui --model '"
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
    if shell == "fish":
        assert "shell,browser" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize(
    ("entry_point", "args", "partial", "expected"),
    [
        ("gptme", ["--model"], "openrouter/", "openrouter/anthropic/"),
        ("gptme-server", ["serve", "--model"], "openrouter/", "openrouter/anthropic/"),
        ("gptme-util", [], "mod", "models"),
        # `gptme chats ...` dispatches to gptme-util, so it completes like it.
        ("gptme", [], "ch", "chats"),
        ("gptme", [], "sea", "search"),
        ("gptme", ["chats"], "l", "list"),
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


def test_custom_model_completion_is_offline(tmp_path, monkeypatch):
    import click

    from gptme.cli.completion import complete_model

    config = tmp_path / "config.toml"
    config.write_text(
        '[[providers]]\nname = "relay"\nbase_url = "https://relay.invalid/v1"\n'
        'default_model = "custom-model"\n'
    )
    monkeypatch.setattr("gptme.config.user.config_path", str(config))

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


def test_non_click_transports_have_no_protocol_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", "/bin/fish")
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    _setup_completions()
    directory = tmp_path / ".config/fish/completions"
    # ACP is a stdio protocol transport: no completion at all.
    assert not (directory / "gptme-acp.fish").exists()
    # Argparse CLIs get static scripts that never call back into the program.
    for name in ARGPARSE_PARSERS:
        content = (directory / f"{name}.fish").read_text()
        assert "_COMPLETE" not in content
        assert f"complete -c {name} -e" in content


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize(
    ("prog", "args", "partial", "expected", "unexpected"),
    [
        ("gptme-convert", [], "", {"convert", "check-tools", "list-formats"}, set()),
        ("gptme-convert", ["convert"], "--q", {"--quality"}, {"convert"}),
        (
            "gptme-convert",
            ["convert", "--quality"],
            "",
            {"low", "medium", "high"},
            set(),
        ),
        ("gptme-convert", ["convert", "--input"], "in", {"input.txt"}, {"low"}),
        ("gptme-eval-trends", [], "--re", {"--results-dir", "--regressions"}, set()),
        ("gptme-eval-trends", ["--format"], "", {"table", "json"}, set()),
        ("gptme-eval-trends", ["--model"], "", set(), {"input.txt"}),
        ("gptme-wut", [], "--l", {"--lines"}, set()),
        ("gptme-wut", ["--lines"], "", set(), {"input.txt"}),
    ],
)
def test_argparse_completion_runs_in_shell_without_running_cli(
    tmp_path, shell, prog, args, partial, expected, unexpected
):
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    parser = load_parser(prog)
    assert parser is not None
    # Completion must never invoke the program: tmux capture, file conversion
    # and eval-result reads are real operations, not completion queries.
    marker = tmp_path / "ran"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / prog).write_text(f"#!/bin/sh\ntouch {marker}\n")
    (bin_dir / prog).chmod(0o755)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "input.txt").write_text("")
    script = generate_argparse_completion(shell, prog, parser)
    assert script is not None
    source = tmp_path / "completion"
    source.write_text(script)
    func = "_" + prog.replace("-", "_") + "_completion"
    words = [prog, *args, partial]
    if shell == "zsh":
        quoted = " ".join(f"'{w}'" for w in words)
        code = (
            f"compdef() {{ :; }}; source {source}; "
            'compadd() { [[ $1 == -- ]] && shift; print -l -- "$@"; }; '
            "_files() { print -l -- ${words[CURRENT]}*(N); }; "
            f"words=({quoted}); CURRENT={len(words)}; {func}"
        )
    elif shell == "fish":
        code = f"source {source}; complete -C '{' '.join(words)}'"
    else:
        quoted = " ".join(f"'{w}'" for w in words)
        code = (
            f"source {source}; COMP_WORDS=({quoted}); COMP_CWORD={len(words) - 1}; "
            f'{func}; printf "%s\\n" "${{COMPREPLY[@]}}"'
        )
    result = subprocess.run(
        [shell, "-c", code],
        check=False,
        text=True,
        capture_output=True,
        cwd=cwd,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    # Fish prints "candidate<TAB>description".
    candidates = {line.split("\t")[0] for line in result.stdout.splitlines()}
    assert expected <= candidates, result.stdout
    assert not unexpected & candidates, result.stdout
    assert not marker.exists()


def test_argparse_parsers_match_cli_behavior():
    # Factoring out build_parser must not change what the CLIs accept.
    from gptme.cli.wut import build_parser as wut
    from gptme.eval.trends import build_parser as trends
    from gptme.tools.convert import build_parser as convert

    assert wut().parse_args(["-l", "5", "--", "-m", "x"]).lines == 5
    args = trends().parse_args(["--format", "json", "--regressions"])
    assert (args.format, args.regressions) == ("json", True)
    args = convert().parse_args(["convert", "-i", "a", "-o", "b.pdf"])
    assert (args.cmd, args.quality, args.dry_run) == ("convert", "medium", False)


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
    monkeypatch.setattr("gptme.cli.main.get_logs_dir", lambda **kw: logs)
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


@pytest.mark.parametrize("content", [None, 'unknown_setting = "keep me"\n', "[broken"])
def test_model_completion_never_writes_config(tmp_path, monkeypatch, content):
    import click

    from gptme.cli.completion import complete_model
    from gptme.config.core import _config_var

    config = tmp_path / "config.toml"
    if content is not None:
        config.write_text(content)
    monkeypatch.setattr("gptme.config.user.config_path", str(config))
    _config_var.set(None)
    values = [
        item.value
        for item in complete_model(
            click.Context(tui), click.Option(["--model"]), "openrouter/"
        )
    ]
    assert any(value.startswith("openrouter/anthropic/") for value in values)
    assert config.exists() == (content is not None)
    if content is not None:
        assert config.read_text() == content


@pytest.mark.parametrize("shell", ["fish", "bash"])
def test_installer_respects_xdg_directories(tmp_path, monkeypatch, shell):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", f"/bin/{shell}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    _setup_completions()
    directory = (
        tmp_path / "config/fish/completions"
        if shell == "fish"
        else tmp_path / "data/bash-completion/completions"
    )
    assert (directory / ("gptme.fish" if shell == "fish" else "gptme")).exists()
    assert not (tmp_path / ".config").exists()
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
@pytest.mark.parametrize(
    ("option", "partial", "expected"),
    [
        ("--tools", "sh", "shell"),
        ("--system", "", "full"),
        ("--system", "", "short"),
        ("--system", "full-n", "full-noexamples"),
    ],
)
def test_legacy_value_completion_protocol(shell, option, partial, expected):
    from gptme.cli.main import main

    env = {
        "_GPTME_COMPLETE": f"{shell}_complete",
        "COMP_WORDS": f"gptme {option} {partial}",
        "COMP_CWORD": partial if shell == "fish" else "2",
    }
    result = CliRunner().invoke(main, [], prog_name="gptme", env=env)
    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_dspy_completion_without_extra_is_quiet(monkeypatch, caplog):
    from gptme.eval.dspy import main

    monkeypatch.setattr("gptme.eval.dspy._has_dspy", lambda: False)
    monkeypatch.setenv("_GPTME_DSPY_COMPLETE", "fish_complete")
    main()
    assert not caplog.records


def test_custom_model_completion_layer_precedence(tmp_path, monkeypatch):
    import click

    from gptme.cli.completion import complete_model

    config = tmp_path / "config.toml"
    runtime = tmp_path / "config.runtime.toml"
    local = tmp_path / "config.local.toml"
    runtime.write_text(
        '[[providers]]\nname = "relay"\nbase_url = "https://relay.invalid/v1"\n'
        'default_model = "runtime-model"\n'
        '[[providers]]\nname = "runtime-only"\nbase_url = "https://runtime.invalid/v1"\n'
        'default_model = "other-model"\n'
    )
    config.write_text('[[providers]]\nname = "relay"\ndefault_model = "main-model"\n')
    local.write_text('[[providers]]\nname = "relay"\ndefault_model = "local-model"\n')
    paths = [runtime, config, local]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    monkeypatch.setattr("gptme.config.user.config_path", str(config))
    values = {
        item.value
        for item in complete_model(click.Context(tui), click.Option(["--model"]), "r")
        if item.value.startswith(("relay", "runtime-only"))
    }
    assert values == {
        "relay",
        "relay/local-model",
        "runtime-only",
        "runtime-only/other-model",
    }
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
    }


def test_dspy_without_extra_still_reports_normal_invocation(monkeypatch, caplog):
    from gptme.eval.dspy import main

    monkeypatch.setattr("gptme.eval.dspy._has_dspy", lambda: False)
    monkeypatch.delenv("_GPTME_DSPY_COMPLETE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        main()
    assert "pip install gptme[dspy]" in caplog.text


@pytest.mark.parametrize(
    "words", ["gptme --tools sh", "gptme --tools shell --model sh"]
)
def test_tools_completion_does_not_initialize_user_config(tmp_path, words):
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "_GPTME_COMPLETE": "fish_complete",
        "COMP_WORDS": words,
        "COMP_CWORD": "sh",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gptme.cli.main import main; main(prog_name='gptme')",
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    if words == "gptme --tools sh":
        assert "shell" in result.stdout
    assert not (tmp_path / ".config/gptme/config.toml").exists()


def test_model_completion_skips_malformed_provider_names(tmp_path, monkeypatch):
    import click

    from gptme.cli.completion import complete_model

    config = tmp_path / "config.toml"
    config.write_text(
        '[[providers]]\nname = ["relay"]\nbase_url = "https://relay.invalid"\n'
    )
    monkeypatch.setattr("gptme.config.user.config_path", str(config))
    values = complete_model(
        click.Context(tui), click.Option(["--model"]), "openrouter/"
    )
    assert values


def test_name_completion_does_not_create_logs(tmp_path, monkeypatch):
    import click

    from gptme.cli.main import ConversationName

    logs = tmp_path / "missing-logs"
    monkeypatch.setenv("GPTME_LOGS_HOME", str(logs))
    assert (
        ConversationName().shell_complete(
            click.Context(tui), click.Option(["--name"]), ""
        )
        == []
    )
    assert not logs.exists()


def test_zsh_instruction_quotes_space_in_path(tmp_path, monkeypatch, capsys):
    import shlex

    from gptme.cli.setup import _install_click_completions

    data = tmp_path / "data with spaces"
    monkeypatch.setattr("gptme.cli.setup.console.width", 1000)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    _install_click_completions("zsh")
    assert shlex.quote(str(data / "zsh/completions")) in capsys.readouterr().out


def test_zsh_instruction_is_plain_unwrapped_text(tmp_path, monkeypatch, capsys):
    """A long path with [brackets] is printed as one pasteable line."""
    import shlex

    from gptme.cli.setup import _install_click_completions

    data = tmp_path / ("long-" * 30 + "[dim]data")
    monkeypatch.setattr("gptme.cli.setup.console.width", 40)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setattr("gptme.cli.setup.Confirm.ask", lambda *a, **kw: True)
    _install_click_completions("zsh")
    expected = f"fpath=({shlex.quote(str(data / 'zsh/completions'))} $fpath)"
    lines = capsys.readouterr().out.splitlines()
    assert any(expected in line for line in lines), lines


@pytest.mark.parametrize(
    ("args", "partial"),
    [([], "README"), (["fix", "this"], "src/"), (["--"], "ch")],
)
def test_prompt_words_fall_back_to_paths(args, partial):
    # Prompts may name files for context; after `--` they are literal prompts,
    # never shortcut dispatch, so no subcommand suggestions either.
    from gptme.cli.main import main as gptme_main

    env = {
        "_GPTME_COMPLETE": "bash_complete",
        "COMP_WORDS": " ".join(["gptme", *args, partial]),
        "COMP_CWORD": str(len(args) + 1),
    }
    result = CliRunner().invoke(gptme_main, [], prog_name="gptme", env=env)
    assert result.exit_code == 0, result.output
    assert result.output.split() == [f"file,{partial}"]
