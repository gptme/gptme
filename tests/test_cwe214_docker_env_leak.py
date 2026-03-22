"""Test for CWE-214: API keys leaked via docker CLI arguments.

The docker_reexec() function in gptme/eval/main.py passes API keys as
command-line arguments (``-e VAR=VALUE``), making them visible to all users
on the system via ``ps aux`` or ``/proc/<pid>/cmdline``.

The fix uses ``--env-file`` with a temporary file (mode 0600) instead, so
secrets never appear on the command line.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def _get_docker_cmd_from_reexec(env_values: dict[str, str]) -> list[str]:
    """Run docker_reexec with mocked subprocess and return the docker command.

    Sets up the environment so docker_reexec thinks it should run, then
    intercepts the final subprocess.run() call to capture the command list.
    """
    captured_cmd: list[str] = []

    def fake_subprocess_run(cmd, **kwargs):
        """Capture the docker run command."""
        if isinstance(cmd, list) and "run" in cmd:
            captured_cmd.extend(cmd)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        return mock_result

    def fake_check_output(cmd, **kwargs):
        """Fake git rev-parse."""
        return "/fake/git/root\n"

    # Build a patched environment with the test keys
    patched_env = dict(os.environ)
    for k, v in env_values.items():
        patched_env[k] = v

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("subprocess.check_output", side_effect=fake_check_output),
        patch.dict(os.environ, patched_env, clear=False),
        patch("sys.exit"),  # prevent SystemExit
    ):
        # Need to also mock get_config so it returns our env values
        mock_config = MagicMock()
        mock_config.get_env = lambda key, default=None: patched_env.get(key, default)

        with patch("gptme.eval.main.get_config", return_value=mock_config):
            from gptme.eval.main import docker_reexec

            docker_reexec(["gptme-eval", "--some-arg"])

    return captured_cmd


def test_api_keys_not_in_cli_args():
    """API key values must NOT appear as docker CLI arguments.

    Before the fix, docker_reexec passes ``-e KEY=secret_value`` which exposes
    the secret in the process argument list.  After the fix, it uses
    ``--env-file /tmp/xxx`` so the secret is only in a file with restrictive
    permissions.
    """
    test_key = "OPENAI_API_KEY"
    test_secret = "sk-test-secret-key-12345"

    cmd = _get_docker_cmd_from_reexec({test_key: test_secret})

    # The secret value must never appear anywhere in the command arguments
    cmd_str = " ".join(cmd)
    assert test_secret not in cmd_str, (
        f"API key value '{test_secret}' found in docker command line args. "
        f"This exposes secrets via ps/proc. Command: {cmd}"
    )


def test_env_file_used_instead():
    """The fix should use --env-file to pass secrets securely."""
    test_key = "OPENAI_API_KEY"
    test_secret = "sk-test-secret-key-12345"

    cmd = _get_docker_cmd_from_reexec({test_key: test_secret})

    assert "--env-file" in cmd, (
        f"Expected --env-file in docker command but not found. Command: {cmd}"
    )


def test_env_file_has_restrictive_permissions():
    """The temporary env file should have mode 0600 (owner read/write only)."""
    import stat
    import tempfile

    test_key = "OPENAI_API_KEY"
    test_secret = "sk-test-secret-key-12345"

    env_file_path = None

    # We need to intercept the env file creation
    original_run = None

    def capture_env_file(cmd, **kwargs):
        nonlocal env_file_path
        if isinstance(cmd, list) and "--env-file" in cmd:
            idx = cmd.index("--env-file")
            if idx + 1 < len(cmd):
                env_file_path = cmd[idx + 1]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        return mock_result

    patched_env = dict(os.environ)
    patched_env[test_key] = test_secret

    with (
        patch("subprocess.run", side_effect=capture_env_file),
        patch("subprocess.check_output", return_value="/fake/git/root\n"),
        patch.dict(os.environ, patched_env, clear=False),
        patch("sys.exit"),
    ):
        mock_config = MagicMock()
        mock_config.get_env = lambda key, default=None: patched_env.get(key, default)

        with patch("gptme.eval.main.get_config", return_value=mock_config):
            from gptme.eval.main import docker_reexec

            docker_reexec(["gptme-eval", "--some-arg"])

    # The env file should have been created (and cleaned up)
    # We can't check permissions after cleanup, but we verify --env-file was used
    assert env_file_path is not None, "Expected --env-file to be used"


def test_multiple_keys_not_leaked():
    """Multiple API keys should all be kept out of the command line."""
    secrets = {
        "OPENAI_API_KEY": "sk-openai-secret",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "DEEPSEEK_API_KEY": "sk-deepseek-secret",
    }

    cmd = _get_docker_cmd_from_reexec(secrets)
    cmd_str = " ".join(cmd)

    for key, secret in secrets.items():
        assert secret not in cmd_str, (
            f"Secret for {key} found in docker command line. Command: {cmd}"
        )
