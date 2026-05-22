"""Regression tests for scripts/github_bot.py."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_github_bot_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "github_bot.py"
    spec = importlib.util.spec_from_file_location("test_github_bot_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


github_bot = _load_github_bot_module()


def test_post_resolve_failure_comment_marks_handled_failure(
    monkeypatch, tmp_path: Path
):
    env_file = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))

    calls: list[list[str]] = []

    def fake_run_command(
        cmd: list[str],
        check: bool = True,
        capture: bool = False,
        cwd: str | None = None,
    ):
        assert check is True
        assert capture is False
        assert cwd is None
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(github_bot, "run_command", fake_run_command)

    github_bot.post_resolve_failure_comment(
        "gptme/gptme",
        2445,
        "The gptme run failed or timed out.",
        token="dummy",
    )

    assert calls == [
        [
            "gh",
            "issue",
            "comment",
            "2445",
            "--repo",
            "gptme/gptme",
            "--body",
            "🤖 **gptme-bot** was unable to resolve this issue. The gptme run failed or timed out.",
        ]
    ]
    assert env_file.read_text(encoding="utf-8") == "GPTME_BOT_HANDLED_FAILURE=1\n"
