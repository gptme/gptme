"""
Tests for Docker execution environments.

These tests verify the DockerGPTMeEnv and DockerClaudeCodeEnv classes for
Docker-isolated eval execution.
Most tests are unit tests that mock Docker to avoid requiring actual Docker runtime.
"""

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.eval.execenv import (
    CLAUDE_CODE_ENV_PASSTHROUGH,
    DOCKER_ENV_PASSTHROUGH,
    DockerClaudeCodeEnv,
    DockerExecutionEnv,
    DockerGPTMeEnv,
    SimpleExecutionEnv,
)


class TestSimpleExecutionEnv:
    """Tests for SimpleExecutionEnv."""

    def test_run_command(self):
        """Test running a simple command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = SimpleExecutionEnv(working_dir=Path(tmpdir))
            stdout, stderr, exit_code = env.run("echo 'hello world'", silent=True)
            assert "hello world" in stdout
            assert exit_code == 0


class TestDockerExecutionEnv:
    """Tests for DockerExecutionEnv."""

    def test_init_default_values(self):
        """Test that default values are set correctly."""
        env = DockerExecutionEnv()
        assert env.image == "gptme-eval:latest"
        assert env.working_dir == "/workspace"
        assert env.host_dir.exists()
        assert env.container_id is None

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_dir = Path(tmpdir)
            env = DockerExecutionEnv(
                image="custom-image:v1",
                working_dir="/custom/workspace",
                host_dir=host_dir,
            )
            assert env.image == "custom-image:v1"
            assert env.working_dir == "/custom/workspace"
            assert env.host_dir == host_dir

    def test_upload_files(self):
        """Test file upload to host directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_dir = Path(tmpdir)
            env = DockerExecutionEnv(host_dir=host_dir)
            env.upload({"test.txt": "hello world"})

            # Check file was written
            assert (host_dir / "test.txt").exists()
            assert (host_dir / "test.txt").read_text() == "hello world"

    def test_download_files(self):
        """Test file download from host directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_dir = Path(tmpdir)
            (host_dir / "output.txt").write_text("output content")

            env = DockerExecutionEnv(host_dir=host_dir)
            files = env.download()

            assert "output.txt" in files
            assert files["output.txt"] == "output content"


class TestDockerGPTMeEnv:
    """Tests for DockerGPTMeEnv - Docker-isolated gptme execution."""

    def test_init_default_values(self):
        """Test that default values are set correctly."""
        env = DockerGPTMeEnv()
        assert env.image == "gptme-eval:latest"
        assert env.working_dir == "/workspace"
        assert env.timeout == 300
        assert env.env_passthrough == DOCKER_ENV_PASSTHROUGH
        assert env.log_dir.exists()

    def test_init_custom_timeout(self):
        """Test initialization with custom timeout."""
        env = DockerGPTMeEnv(timeout=600)
        assert env.timeout == 600

    def test_init_custom_env_passthrough(self):
        """Test initialization with custom env passthrough list."""
        custom_vars = ["CUSTOM_API_KEY", "CUSTOM_CONFIG"]
        env = DockerGPTMeEnv(env_passthrough=custom_vars)
        assert env.env_passthrough == custom_vars

    def test_get_env_args_empty(self):
        """Test env args when no matching env vars are set."""
        env = DockerGPTMeEnv(env_passthrough=["NONEXISTENT_VAR"])
        args = env._get_env_args()
        assert args == []

    def test_get_env_args_with_values(self):
        """Test env args pass variable name only (no value in cmdline)."""
        with patch.dict(os.environ, {"TEST_API_KEY": "secret123"}):
            env = DockerGPTMeEnv(env_passthrough=["TEST_API_KEY"])
            args = env._get_env_args()
            assert args == ["-e", "TEST_API_KEY"]

    @patch("subprocess.run")
    def test_start_container_success(self, mock_run):
        """Test successful container start."""
        mock_run.return_value = MagicMock(stdout="container123\n", returncode=0)

        env = DockerGPTMeEnv()
        env.start_container()

        assert env.container_id == "container123"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_start_container_image_not_found(self, mock_run):
        """Test container start with missing image."""
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(
            1, "docker", stderr="Unable to find image"
        )

        env = DockerGPTMeEnv()
        with pytest.raises(RuntimeError, match="Docker image not found"):
            env.start_container()

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_run_gptme_builds_correct_command(self, mock_popen, mock_run):
        """Test that run_gptme builds the correct CLI command."""
        # Mock container start
        mock_run.return_value = MagicMock(stdout="container123\n", returncode=0)

        # Mock Popen for command execution
        mock_process = MagicMock()
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        env = DockerGPTMeEnv()
        env.run_gptme(
            prompt="Hello, world!",
            model="openai/gpt-4o",
            tool_format="markdown",
        )

        # Verify Popen was called with docker exec
        assert mock_popen.called
        call_args = mock_popen.call_args[0][0]
        assert call_args[0] == "docker"
        assert call_args[1] == "exec"

    def test_default_env_passthrough_includes_major_providers(self):
        """Test that default env passthrough includes major API providers."""
        assert "OPENAI_API_KEY" in DOCKER_ENV_PASSTHROUGH
        assert "ANTHROPIC_API_KEY" in DOCKER_ENV_PASSTHROUGH
        assert "GOOGLE_API_KEY" in DOCKER_ENV_PASSTHROUGH
        assert "GROQ_API_KEY" in DOCKER_ENV_PASSTHROUGH
        assert "OPENROUTER_API_KEY" in DOCKER_ENV_PASSTHROUGH

    def test_log_dir_created(self):
        """Test that log directory is created on init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_dir = Path(tmpdir)
            log_dir = host_dir / "custom_logs"

            env = DockerGPTMeEnv(host_dir=host_dir, log_dir=log_dir)
            assert env.log_dir == log_dir
            assert log_dir.exists()

    def test_get_logs_empty(self):
        """Test get_logs when no logs exist."""
        env = DockerGPTMeEnv()
        logs = env.get_logs()
        assert logs == {}

    def test_get_logs_with_files(self):
        """Test get_logs when log files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_dir = Path(tmpdir)
            log_dir = host_dir / "logs"
            log_dir.mkdir()

            # Create a mock log file
            (log_dir / "test.jsonl").write_text('{"msg": "test"}\n')

            env = DockerGPTMeEnv(host_dir=host_dir, log_dir=log_dir)
            logs = env.get_logs()

            assert "test.jsonl" in logs
            assert '{"msg": "test"}' in logs["test.jsonl"]


class TestDockerClaudeCodeEnv:
    """Tests for DockerClaudeCodeEnv - Docker-isolated Claude Code execution."""

    def test_init_default_values(self):
        """Test that default values are set correctly."""
        env = DockerClaudeCodeEnv()
        assert env.image == "gptme-eval:latest"
        assert env.working_dir == "/workspace"
        assert env.timeout == 600
        assert env.env_passthrough == CLAUDE_CODE_ENV_PASSTHROUGH

    def test_get_env_args_with_values(self):
        """Test env args when Anthropic API key is set.

        Uses ``-e VAR`` (no value) so the secret stays out of ps/cmdline."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret123"}):
            env = DockerClaudeCodeEnv()
            args = env._get_env_args()
            assert args == ["-e", "ANTHROPIC_API_KEY"]

    @patch("subprocess.run")
    def test_start_container_timeout_raises(self, mock_run):
        """Test that start_container raises RuntimeError on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="docker run -d ...", timeout=120
        )

        env = DockerClaudeCodeEnv()
        with pytest.raises(RuntimeError, match="startup timed out"):
            env.start_container()

    @patch("subprocess.run")
    def test_start_container_image_not_found(self, mock_run):
        """Test container start with missing image includes Claude install hint."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "docker", stderr="Unable to find image"
        )

        env = DockerClaudeCodeEnv()
        with pytest.raises(RuntimeError, match="Docker image not found") as exc_info:
            env.start_container()

        assert "Claude Code CLI" in str(exc_info.value)

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_run_claude_code_builds_correct_command(self, mock_popen, mock_run):
        """Test that run_claude_code builds a shell-quoted Claude CLI command."""
        mock_run.return_value = MagicMock(stdout="container123\n", returncode=0)
        mock_process = MagicMock()
        mock_process.communicate.return_value = ('{"type":"result"}\n', "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        env = DockerClaudeCodeEnv()
        prompt = "fix 'this' file"
        tools = ["shell", "read write"]
        env.run_claude_code(
            prompt=prompt,
            model="claude-sonnet-4-6",
            max_turns=42,
            tools=tools,
        )

        mock_popen.assert_called_once()
        docker_exec_cmd = mock_popen.call_args[0][0]
        assert docker_exec_cmd[:5] == ["docker", "exec", "-i", "-w", "/workspace"]
        assert docker_exec_cmd[5] == "container123"
        command = docker_exec_cmd[-1]
        assert "claude -p" in command
        assert shlex.quote(prompt) in command
        assert shlex.quote("claude-sonnet-4-6") in command
        assert "--max-turns 42" in command
        assert shlex.quote(",".join(tools)) in command

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_run_claude_code_timeout_stops_container_and_raises(
        self, mock_popen, mock_run
    ):
        """Test timeout path stops the container and re-raises TimeoutExpired."""
        mock_run.side_effect = [
            MagicMock(stdout="container123\n", returncode=0),
            MagicMock(returncode=0),
        ]

        mock_process = MagicMock()
        mock_process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="claude -p", timeout=600),
            ("partial stdout", "partial stderr"),
        ]
        mock_process.returncode = 137
        mock_popen.return_value = mock_process

        env = DockerClaudeCodeEnv(timeout=600)

        with pytest.raises(subprocess.TimeoutExpired):
            env.run_claude_code(prompt="test", model="claude-sonnet-4-6")

        mock_process.kill.assert_called_once()
        assert mock_run.call_args_list[1][0][0] == ["docker", "stop", "container123"]


class TestDockerGPTMeEnvIntegration:
    """
    Integration tests for DockerGPTMeEnv.

    These tests require Docker to be available and the gptme-eval image built.
    Mark as slow and skip if Docker is not available.
    """

    @pytest.fixture
    def skip_if_no_docker(self):
        """Skip test if Docker is not available."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker", "info"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                pytest.skip("Docker not available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("Docker not available")

    @pytest.fixture
    def skip_if_no_image(self, skip_if_no_docker):
        """Skip test if gptme-eval image is not built."""
        import subprocess

        result = subprocess.run(
            ["docker", "images", "-q", "gptme-eval:latest"],
            check=False,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            pytest.skip("gptme-eval:latest image not built")

    @pytest.mark.slow
    @pytest.mark.integration
    def test_run_simple_command_in_container(self, skip_if_no_image):
        """Test running a simple command in the container."""
        env = DockerGPTMeEnv()
        try:
            stdout, stderr, exit_code = env.run("echo 'hello from docker'")
            assert "hello from docker" in stdout
            assert exit_code == 0
        finally:
            env.cleanup()

    @pytest.mark.slow
    @pytest.mark.integration
    def test_file_roundtrip_through_container(self, skip_if_no_image):
        """Test uploading and downloading files through container."""
        env = DockerGPTMeEnv()
        try:
            # Upload a file
            env.upload({"input.txt": "test input content"})

            # Run a command that modifies the file
            env.run("cat input.txt > output.txt && echo ' modified' >> output.txt")

            # Download files
            files = env.download()
            assert "output.txt" in files
            assert "test input content" in files["output.txt"]
            assert "modified" in files["output.txt"]
        finally:
            env.cleanup()
