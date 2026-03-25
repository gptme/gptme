"""Test for CWE-214: API keys leaked via docker CLI arguments.

The docker_reexec() function in gptme/eval/main.py passes API keys as
command-line arguments (``-e VAR=VALUE``), making them visible to all users
on the system via ``ps aux`` or ``/proc/<pid>/cmdline``.

The fix uses ``--env-file`` with a temporary file (mode 0600) instead, so
secrets never appear on the command line.
"""

import importlib
import os
import stat
from unittest.mock import MagicMock, patch

# gptme/eval/__init__.py does ``from .main import main``, which shadows the
# module name with the Click Command object.  Using importlib guarantees we
# get the *module*, not the Click command — this is required for patch.object.
_eval_main_mod = importlib.import_module("gptme.eval.main")


def _get_docker_cmd_from_reexec(env_values: dict[str, str]) -> list[str]:
    """Run docker_reexec with mocked subprocess and return the docker command.

    Sets up the environment so docker_reexec thinks it should run, then
    intercepts the final subprocess.run() call to capture the command list.
    """
    captured_cmd: list[str] = []

    def fake_subprocess_run(cmd, **kwargs):
        """Capture the docker run command."""
        nonlocal captured_cmd
        if isinstance(cmd, list) and "run" in cmd:
            captured_cmd = list(cmd)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        return mock_result

    def fake_check_output(cmd, **kwargs):
        """Fake git rev-parse."""
        return "/fake/git/root\n"

    # Build a patched environment with the test keys
    patched_env = {**os.environ, **env_values}

    # Mock get_config so it returns our env values.
    # Patch at gptme.eval.main.get_config (where docker_reexec looks it up)
    # *and* at gptme.config.get_config (the canonical location) so the mock
    # is effective regardless of import resolution order in CI workers.
    mock_config = MagicMock()
    mock_config.get_env = lambda key, default=None: patched_env.get(key, default)

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("subprocess.check_output", side_effect=fake_check_output),
        patch.dict(os.environ, patched_env, clear=False),
        patch("sys.exit"),  # prevent SystemExit
        patch.object(_eval_main_mod, "get_config", return_value=mock_config),
        patch("gptme.config.get_config", return_value=mock_config),
    ):
        docker_reexec = _eval_main_mod.docker_reexec
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
    """The temporary env file should have mode 0600 (owner read/write only).

    Permissions are checked *inside* the subprocess.run mock while the file
    still exists on disk (before the ``finally: os.unlink`` cleanup runs).
    """
    test_key = "OPENAI_API_KEY"
    test_secret = "sk-test-secret-key-12345"

    env_file_path = None
    env_file_mode = None

    def capture_env_file(cmd, **kwargs):
        nonlocal env_file_path, env_file_mode
        if isinstance(cmd, list) and "--env-file" in cmd:
            idx = cmd.index("--env-file")
            if idx + 1 < len(cmd):
                env_file_path = cmd[idx + 1]
                # File still exists here — check permissions now
                env_file_mode = stat.S_IMODE(os.stat(env_file_path).st_mode)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        return mock_result

    patched_env = {**os.environ, test_key: test_secret}
    mock_config = MagicMock()
    mock_config.get_env = lambda key, default=None: patched_env.get(key, default)

    with (
        patch("subprocess.run", side_effect=capture_env_file),
        patch("subprocess.check_output", return_value="/fake/git/root\n"),
        patch.dict(os.environ, patched_env, clear=False),
        patch("sys.exit"),
        patch.object(_eval_main_mod, "get_config", return_value=mock_config),
        patch("gptme.config.get_config", return_value=mock_config),
    ):
        docker_reexec = _eval_main_mod.docker_reexec
        docker_reexec(["gptme-eval", "--some-arg"])

    assert env_file_path is not None, "Expected --env-file to be used"
    # The production code calls os.chmod(path, 0o600) after writing,
    # so the mode is unconditionally 0o600 regardless of umask.
    assert env_file_mode == 0o600, (
        f"Expected env file permissions 0o600, got 0o{env_file_mode:o}"
    )


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


def test_env_file_contains_expected_content():
    """The temporary env file should contain the expected KEY=VALUE entries.

    Content is verified *inside* the subprocess.run mock while the file
    still exists on disk (before cleanup).
    """
    test_secrets = {
        "OPENAI_API_KEY": "sk-test-openai-key",
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
    }

    env_file_content = None

    def capture_env_file_content(cmd, **kwargs):
        nonlocal env_file_content
        if isinstance(cmd, list) and "--env-file" in cmd:
            idx = cmd.index("--env-file")
            if idx + 1 < len(cmd):
                with open(cmd[idx + 1]) as f:
                    env_file_content = f.read()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        return mock_result

    patched_env = {**os.environ, **test_secrets}
    mock_config = MagicMock()
    mock_config.get_env = lambda key, default=None: patched_env.get(key, default)

    with (
        patch("subprocess.run", side_effect=capture_env_file_content),
        patch("subprocess.check_output", return_value="/fake/git/root\n"),
        patch.dict(os.environ, patched_env, clear=False),
        patch("sys.exit"),
        patch.object(_eval_main_mod, "get_config", return_value=mock_config),
        patch("gptme.config.get_config", return_value=mock_config),
    ):
        docker_reexec = _eval_main_mod.docker_reexec
        docker_reexec(["gptme-eval", "--some-arg"])

    assert env_file_content is not None, "Expected env file to be written"
    for key, value in test_secrets.items():
        expected_line = f"{key}={value}"
        assert expected_line in env_file_content, (
            f'Expected "{expected_line}" in env file content, got: {env_file_content}'
        )
