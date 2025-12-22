"""
Tests for Docker execution environments.

These tests verify the DockerGPTMeEnv class for Docker-isolated gptme execution.
Most tests are unit tests that mock Docker to avoid requiring actual Docker runtime.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.eval.execenv import (
    DOCKER_ENV_PASSTHROUGH,
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
        """Test env args when env vars are set."""
        with patch.dict(os.environ, {"TEST_API_KEY": "secret123"}):
            env = DockerGPTMeEnv(env_passthrough=["TEST_API_KEY"])
            args = env._get_env_args()
            assert args == ["-e", "TEST_API_KEY=secret123"]

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
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.return_value = ""
        mock_process.stderr.readline.return_value = ""
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
