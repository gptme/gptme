"""Parse captured restart commands without invoking entry-point callbacks or exec."""

import sys
from unittest.mock import patch

import click
import pytest

from gptme.cli.main import main as cli
from gptme.tools.restart import _do_restart
from gptme.tui.main import main as tui


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--output-format", "json"], {"output_format": "json"}),
        (["--allow-hosts", "example.test"], {"allow_hosts": "example.test"}),
        (["--output-format=json"], {"output_format": "json"}),
        (["--allow-hosts=example.test"], {"allow_hosts": "example.test"}),
        (["--system", "--verbose"], {"prompt_system": "--verbose", "verbose": False}),
        (["--context-budget", "0.75"], {"context_budget": 0.75}),
        (["--agent-profile", "researcher"], {"agent_profile": "researcher"}),
        (["--output-schema", "example:Schema"], {"output_schema": "example:Schema"}),
        (["-vy"], {"verbose": True, "no_confirm": True}),
        (["-nv"], {"verbose": True, "non_interactive": False}),
        (["-vmold-model"], {"verbose": True, "model": None}),
        (["--context", "files", "--context-include=cmd"], {"context_include": ()}),
        (["--name=old", "--model=old-model", "-t", "shell"], {"model": None}),
        (["--no-stream", "--show-hidden"], {"stream": True, "show_hidden": True}),
        (
            ["--no-prune-tool-output", "--no-workspace"],
            {"prune_tool_output": False, "no_workspace": True},
        ),
        (
            ["prompt", "--", "--allow-hosts", "prompt.test", "--verbose"],
            {"allow_hosts": None, "verbose": False},
        ),
    ],
)
def test_cli_restart_parses_retained_options(
    args: list[str], expected: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The session launcher can supply Click defaults; keep these parse-only tests hermetic.
    monkeypatch.delenv("GPTME_ALLOW_HOSTS", raising=False)
    monkeypatch.setattr(
        cli, "context_settings", {**cli.context_settings, "auto_envvar_prefix": None}
    )
    with (
        patch.object(sys, "argv", ["gptme", *args, "original prompt"]),
        patch("gptme.tools.restart._cleanup_before_handover") as cleanup,
        patch("gptme.tools.restart.os.execv") as execv,
    ):
        _do_restart("synthetic-resume")
    cleanup.assert_called_once_with()
    executable, argv = execv.call_args.args
    assert executable == argv[0] == "gptme"
    with cli.make_context("gptme", argv[1:]) as ctx:
        assert ctx.params["name"] == "synthetic-resume"
        assert not ctx.params["prompts"]
        for key, value in expected.items():
            assert ctx.params[key] == value


@pytest.mark.parametrize(
    "args",
    [
        ["-n", "old"],
        ["-nold"],
        ["-vnold"],
        ["-r", "old"],
        ["-rold"],
        ["-r--inline"],
        ["-r-v"],
        ["-vr--inline"],
        ["-r=--inline"],
        ["--resume=old"],
        ["--resume", "--inline"],
        ["-r", "--inline"],
        ["--", "--resume", "old"],
    ],
)
def test_tui_restart_selects_current_conversation(args: list[str]) -> None:
    with (
        patch.object(sys, "argv", ["gptme-tui", *args]),
        patch("gptme.tools.restart._cleanup_before_handover"),
        patch("gptme.tools.restart.os.execv") as execv,
    ):
        _do_restart("synthetic-resume", source="tui")
    argv = execv.call_args.args[1]
    with tui.make_context("gptme-tui", args[:]) as original:
        expected_inline = original.params["inline"]
        expected_verbose = original.params["verbose"]
    with tui.make_context("gptme-tui", argv[1:]) as ctx:
        assert ctx.params["name"] == "synthetic-resume"
        assert ctx.params["resume"] is None
        assert not ctx.params["prompts"]
        assert ctx.params["inline"] == expected_inline
        assert ctx.params["verbose"] == expected_verbose


def test_cli_value_options_are_derived_from_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly declared value-taking flag needs no restart-table update."""
    option = click.Option(["--synthetic-value"])
    monkeypatch.setattr(cli, "params", [*cli.params, option])
    with (
        patch.object(sys, "argv", ["gptme", "--synthetic-value", "--verbose"]),
        patch("gptme.tools.restart._cleanup_before_handover"),
        patch("gptme.tools.restart.os.execv") as execv,
    ):
        _do_restart("synthetic-resume")
    assert execv.call_args.args[1] == [
        "gptme",
        "--synthetic-value",
        "--verbose",
        "--name",
        "synthetic-resume",
    ]
