"""Secret values printed by shell commands must not reach the conversation.

Regression for an accidental ``env`` dump: a script's comment line broke a
backslash-continued ``env -u ... cmd`` so ``env`` ran bare and printed every
credential in the environment into the tool result.
"""

import pytest

import gptme.tools.shell as shell_module
from gptme.tools.shell import execute_shell
from gptme.util.redact import redact_secret_values_from_output

# Built at runtime so the literal never looks like a committed credential.
GH_TOKEN = "gh" + "p_" + "A1b2C3d4" * 5
OR_KEY = "sk-" + "or-v1-" + "0123456789abcdef" * 4
OPAQUE = "Zq8" + "x7WmK2pL9vR4tY6uN3bH5jC1"


def test_env_dump_values_are_redacted():
    text = (
        f"GITHUB_TOKEN={GH_TOKEN}\n"
        f"OPENROUTER_API_KEY={OR_KEY}\n"
        f"export SERVICE_SECRET='{OPAQUE}'\n"
        "HOME=/home/bob\n"
    )
    out, n = redact_secret_values_from_output(text)
    assert n == 3
    assert GH_TOKEN not in out and OR_KEY not in out and OPAQUE not in out
    assert "GITHUB_TOKEN=[REDACTED]\n" in out
    assert "HOME=/home/bob\n" in out


def test_yaml_style_value_is_redacted():
    out, n = redact_secret_values_from_output(f"  github_token: {GH_TOKEN}\n")
    assert n == 1
    assert out == "  github_token: [REDACTED]\n"


@pytest.mark.parametrize(
    "line",
    [
        "TOKEN_LIMIT = 4096\n",
        'API_KEY = os.environ["OPENAI_API_KEY"]\n',
        'GITHUB_TOKEN="$(gh auth token)"\n',
        "password: ${DB_PASSWORD}\n",
        "max_tokens: 1024\n",
        'GITHUB_TOKEN = "ghp_" + secrets.token_hex(20)\n',
        'GITHUB_TOKEN = "ghp_" + suffix\n',
        "API_KEY = sk_placeholder()\n",
    ],
)
def test_source_code_lines_are_kept(line):
    """Shell output often holds source code the agent will edit next."""
    out, n = redact_secret_values_from_output(line)
    assert n == 0
    assert out == line


@pytest.mark.parametrize(
    "value",
    [
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.aB3_dE-fG4hI5jK6",
        "Zq8_x7Wm-K2pL9vR4tY6uN3bH5jC1",
        "0123456789abcdef0123456789abcdef",
    ],
)
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_output_specific_credentials(value, quote):
    out, n = redact_secret_values_from_output(f"SERVICE_TOKEN={quote}{value}{quote}\n")
    assert n == 1
    assert value not in out
    assert out == "SERVICE_TOKEN=[REDACTED]\n"


@pytest.mark.parametrize("final", [False, True])
def test_background_display_and_logs_are_redacted(tmp_path, final):
    from unittest.mock import Mock

    from gptme.tools.shell_background import (
        BackgroundJob,
        _write_job_log,
        format_job_output,
    )

    stdout = f"\x1b[32mGITHUB_TOKEN={GH_TOKEN}\x1b[0m\n" + "ok\n" * 3000
    stderr = f"API_KEY={OR_KEY}\n"
    job = BackgroundJob(
        id=1,
        command="synthetic-output",
        process=Mock(),
        start_time=0,
        stdout_buffer=[stdout],
        stderr_buffer=[stderr],
        log_dir=tmp_path,
    )
    if final:
        _write_job_log(job, stdout, stderr, final=True)
    rendered = format_job_output(job, stdout, stderr)
    assert job.log_path is not None
    saved = job.log_path.read_text()
    for text in (rendered, saved):
        assert GH_TOKEN not in text
        assert OR_KEY not in text
        assert "API_KEY=[REDACTED]" in text
    assert "GITHUB_TOKEN=[REDACTED]" in saved
    assert "Redacted secret values on 2 output lines" in rendered


def test_background_redaction_opt_out(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from gptme.tools.shell_background import (
        BackgroundJob,
        _write_job_log,
        format_job_output,
    )

    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "0")
    text = f"GITHUB_TOKEN={GH_TOKEN}\n"
    job = BackgroundJob(
        id=1, command="synthetic-output", process=Mock(), start_time=0, log_dir=tmp_path
    )
    assert GH_TOKEN in format_job_output(job, text, "")
    path = _write_job_log(job, text, "", final=True)
    assert path is not None and GH_TOKEN in path.read_text()


def _run(monkeypatch, command: str) -> str:
    monkeypatch.setattr(
        shell_module, "check_with_shellcheck", lambda cmd: (False, False, "")
    )
    messages = list(execute_shell(None, None, {"command": command}))
    return "\n".join(m.content for m in messages)


def test_shell_tool_redacts_env_dump(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GH_TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", OR_KEY)
    result = _run(monkeypatch, "env | grep -E '^(GITHUB_TOKEN|OPENROUTER_API_KEY)='")

    assert GH_TOKEN not in result
    assert OR_KEY not in result
    assert "GITHUB_TOKEN=[REDACTED]" in result
    assert "Redacted secret values on 2 output lines" in result


def test_shell_tool_redacts_stderr(monkeypatch):
    monkeypatch.setenv("FAKE_VALUE", OR_KEY)
    result = _run(monkeypatch, 'echo "API_KEY=$FAKE_VALUE" >&2')
    assert OR_KEY not in result
    assert "API_KEY=[REDACTED]" in result


def test_shell_tool_redaction_opt_out(monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "0")
    monkeypatch.setenv("FAKE_VALUE", GH_TOKEN)
    result = _run(monkeypatch, 'echo "GITHUB_TOKEN=$FAKE_VALUE"')
    assert GH_TOKEN in result
    assert "Redacted secret values" not in result


@pytest.mark.parametrize("promote", [False, True])
def test_real_background_output_is_redacted(monkeypatch, tmp_path, promote):
    """Promotion, explicit background, output/wait and completion share the guard."""
    from gptme.tools.shell_background import (
        background_job_completion_hook,
        execute_output_command,
        execute_wait_command,
        list_background_jobs,
        reset_background_jobs,
    )

    reset_background_jobs()
    monkeypatch.setenv("FAKE_VALUE", GH_TOKEN)
    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "0.25")
    monkeypatch.setattr(
        shell_module, "check_with_shellcheck", lambda cmd: (False, False, "")
    )
    shell = shell_module.ShellSession(cwd=str(tmp_path))
    shell_module.set_shell(shell)
    try:
        messages = list(
            execute_shell(
                None,
                None,
                {
                    "command": 'printf "GITHUB_TOKEN=%s\\n" "$FAKE_VALUE"; sleep 1',
                    "background": "false" if promote else "true",
                },
            )
        )
        job = list_background_jobs()[0]
        messages.extend(execute_output_command(str(job.id)))
        messages.extend(execute_wait_command(str(job.id), "5"))
        messages.extend(background_job_completion_hook(None))
        result = "\n".join(m.content for m in messages)
        assert GH_TOKEN not in result
        assert "GITHUB_TOKEN=[REDACTED]" in result
        if promote:
            assert "Output so far" in messages[0].content
            assert "GITHUB_TOKEN=[REDACTED]" in messages[0].content
    finally:
        reset_background_jobs()
        shell.close()
        shell_module.get_shell().close()


@pytest.mark.parametrize("ending", ["\r\n", "\r", "\n", ""])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_output_preserves_line_endings(ending, quote):
    out, n = redact_secret_values_from_output(
        f"GITHUB_TOKEN={quote}{GH_TOKEN}{quote}{ending}"
    )
    assert n == 1
    assert out == f"GITHUB_TOKEN=[REDACTED]{ending}"


def test_incremental_output_retains_assignment_context(monkeypatch):
    from unittest.mock import Mock

    import gptme.tools.shell_background as background

    job = background.BackgroundJob(
        id=1,
        command="synthetic-output",
        process=Mock(),
        start_time=0,
        stdout_buffer=["GITHUB_TOKEN="],
        stderr_buffer=["SERVICE_TOKEN="],
    )
    monkeypatch.setattr(background, "get_background_job", lambda _: job)
    first = list(background.execute_output_command("1 --new"))
    job.stdout_buffer.append(GH_TOKEN + "\n")
    job.stderr_buffer.append(OR_KEY + "\n")
    second = list(background.execute_output_command("1 --new"))
    rendered = "\n".join(m.content for m in first + second)
    assert GH_TOKEN not in rendered
    assert OR_KEY not in rendered
    assert "GITHUB_TOKEN=[REDACTED]" in rendered
    assert "SERVICE_TOKEN=[REDACTED]" in rendered
    third = list(background.execute_output_command("1 --new"))
    assert "No new output" in third[0].content


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_dotenv_comment_does_not_hide_credential(quote):
    text = f"GITHUB_TOKEN={quote}{GH_TOKEN}{quote} # production\n"
    out, n = redact_secret_values_from_output(text)
    assert n == 1
    assert GH_TOKEN not in out


def test_partial_background_credential_is_not_published():
    from unittest.mock import Mock

    from gptme.tools.shell_background import BackgroundJob, format_job_output

    prefix = OPAQUE[:20]
    job = BackgroundJob(id=1, command="synthetic", process=Mock(), start_time=0)
    result = format_job_output(job, f"SERVICE_TOKEN={prefix}", "")
    assert prefix not in result


def test_evicted_assignment_context_cannot_leak_to_log(tmp_path):
    from unittest.mock import Mock

    from gptme.tools.shell_background import (
        _MAX_BUFFER_SIZE,
        BackgroundJob,
        format_job_output,
    )

    job = BackgroundJob(
        id=1, command="synthetic", process=Mock(), start_time=0, log_dir=tmp_path
    )
    for chunk in (
        "GITHUB_TOKEN=",
        GH_TOKEN + "\n",
        "ok\n" * (_MAX_BUFFER_SIZE // 3 - 15),
    ):
        job._stdout_buffer_start += job._append_to_buffer(job.stdout_buffer, chunk)
    assert job.get_output()[0].startswith(GH_TOKEN)
    result = format_job_output(job, *job.get_output())
    assert GH_TOKEN not in result
    assert job.log_path is not None
    assert GH_TOKEN not in job.log_path.read_text()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_incremental_output_after_eviction_keeps_new_lines(monkeypatch, stream):
    from unittest.mock import Mock

    import gptme.tools.shell_background as background

    job = background.BackgroundJob(
        id=1, command="synthetic", process=Mock(), start_time=0
    )
    buffer = getattr(job, f"{stream}_buffer")
    buffer.append(GH_TOKEN + "\nold output\n")
    setattr(job, f"_{stream}_buffer_start", 13)
    monkeypatch.setattr(background, "get_background_job", lambda _: job)
    first = list(background.execute_output_command("1 --new"))[0].content
    assert GH_TOKEN not in first
    assert "old output" in first
    buffer.append("build succeeded\n")
    second = list(background.execute_output_command("1 --new"))[0].content
    assert "build succeeded" in second
    assert "prefix was evicted" not in second
    third = list(background.execute_output_command("1 --new"))[0].content
    assert "No new output" in third
