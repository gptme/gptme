import base64
import os
import shlex
import subprocess
import tempfile
import time
from abc import abstractmethod
from pathlib import Path

from .filestore import Files, FileStore


class ExecutionEnv:
    @abstractmethod
    def run(self, command: str):
        """
        Runs a command in the execution environment.
        """
        raise NotImplementedError

    @abstractmethod
    def upload(self, files: Files):
        """
        Uploads files to the execution environment.
        """
        raise NotImplementedError

    @abstractmethod
    def download(self) -> Files:
        """
        Downloads files from the execution environment.
        """
        raise NotImplementedError


class SimpleExecutionEnv(FileStore, ExecutionEnv):
    """
    A simple execution environment that runs the code in the files.

    upload() and download() are inherited from FileStore.
    """

    def run(self, command, silent=True) -> tuple[str, str, int]:
        start = time.time()
        if not silent:
            print("\n--- Start of run ---")
        # Use explicit shell invocation with list-based arguments for security.
        # This avoids shell=True which can be vulnerable to shell injection.
        # The command is passed to bash -c, similar to DockerExecutionEnv.
        p = subprocess.Popen(
            ["/bin/bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.working_dir,
            text=True,
        )
        if not silent:
            print("$", command)
        stdout_full, stderr_full = "", ""
        while p.poll() is None or p.stdout or p.stderr:
            assert p.stdout is not None
            assert p.stderr is not None
            stdout = p.stdout.readline()
            stderr = p.stderr.readline()
            if stdout:
                if not silent:
                    print(stdout, end="")
                stdout_full += stdout
            if stderr:
                if not silent:
                    print(stderr, end="")
                stderr_full += stderr
            if not stdout and not stderr and p.poll() is not None:
                break
            if time.time() - start > 30:
                if not silent:
                    print("Timeout!")
                p.kill()
                break
        if not silent:
            print("--- Finished run ---\n")
        return stdout_full, stderr_full, p.returncode


class DockerExecutionEnv(ExecutionEnv):
    """
    Docker-based execution environment for isolated command execution.

    Prevents host environment pollution (e.g., git config) by running all
    commands inside a Docker container. Uses volume mounts for file transfer.

    Args:
        image: Docker image to use (default: gptme-eval:latest)
        working_dir: Working directory inside container (default: /workspace)
        host_dir: Host directory to mount (default: temp directory)
    """

    def __init__(
        self,
        image: str = "gptme-eval:latest",
        working_dir: str = "/workspace",
        host_dir: Path | None = None,
    ):
        self.image = image
        self.working_dir = working_dir
        self.host_dir = host_dir or Path(tempfile.mkdtemp(prefix="gptme-docker-"))
        self.host_dir.mkdir(parents=True, exist_ok=True)
        self.container_id: str | None = None

    def start_container(self) -> None:
        """Start Docker container with volume mount."""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "-v",
                    f"{self.host_dir}:{self.working_dir}",
                    self.image,
                    "tail",
                    "-f",
                    "/dev/null",  # Keep container alive
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.container_id = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to start Docker container with image '{self.image}'.\n"
            if "Unable to find image" in e.stderr or "No such image" in e.stderr:
                error_msg += "Docker image not found. Build it with: make build-docker"
            else:
                error_msg += f"Error: {e.stderr}"
            raise RuntimeError(error_msg) from e

    def run(self, command: str, silent: bool = True) -> tuple[str, str, int]:
        """Execute command inside Docker container."""
        if not self.container_id:
            self.start_container()

        # Ensure container_id is not None (mypy type narrowing)
        assert self.container_id is not None

        start = time.time()
        if not silent:
            print("\n--- Start of run (Docker) ---")
            print("$", command)

        # Execute command in Docker container
        p = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                "-w",
                self.working_dir,
                self.container_id,
                "/bin/bash",
                "-c",
                command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stdout_full, stderr_full = "", ""
        while p.poll() is None or p.stdout or p.stderr:
            assert p.stdout is not None
            assert p.stderr is not None
            stdout = p.stdout.readline()
            stderr = p.stderr.readline()
            if stdout:
                if not silent:
                    print(stdout, end="")
                stdout_full += stdout
            if stderr:
                if not silent:
                    print(stderr, end="")
                stderr_full += stderr
            if not stdout and not stderr and p.poll() is not None:
                break
            if time.time() - start > 30:
                if not silent:
                    print("Timeout!")
                p.kill()
                # Stop container to terminate the running command
                if self.container_id:
                    subprocess.run(
                        ["docker", "stop", self.container_id],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                break

        if not silent:
            print("--- Finished run (Docker) ---\n")

        return stdout_full, stderr_full, p.returncode

    def upload(self, files: Files) -> None:
        """Upload files to container via mounted host directory."""
        for name, content in files.items():
            path = self.host_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                with open(path, "w") as f:
                    f.write(content)
            elif isinstance(content, bytes):
                with open(path, "wb") as f:
                    f.write(base64.b64decode(content))

    def download(self) -> Files:
        """Download files from container via mounted host directory."""
        files: Files = {}
        for path in self.host_dir.glob("**/*"):
            if path.is_file():
                rel_path = path.relative_to(self.host_dir)
                try:
                    with open(path) as f:
                        files[str(rel_path)] = f.read()
                except UnicodeDecodeError:
                    with open(path, "rb") as f:
                        files[str(rel_path)] = base64.b64encode(f.read())
        return files

    def cleanup(self) -> None:
        """Stop and remove Docker container."""
        if self.container_id:
            subprocess.run(
                ["docker", "stop", self.container_id],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["docker", "rm", self.container_id],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def __del__(self) -> None:
        """Cleanup container on object destruction."""
        self.cleanup()


# Environment variable passthrough configuration
# These are the API keys and config vars that should be passed to Docker containers
DOCKER_ENV_PASSTHROUGH = [
    # OpenAI
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    # Anthropic
    "ANTHROPIC_API_KEY",
    # Google
    "GOOGLE_API_KEY",
    # Groq
    "GROQ_API_KEY",
    # OpenRouter
    "OPENROUTER_API_KEY",
    # Azure OpenAI
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    # Deepseek
    "DEEPSEEK_API_KEY",
    # xAI
    "XAI_API_KEY",
    # Local/Custom
    "OLLAMA_BASE_URL",
    # gptme specific
    "GPTME_MODEL",
    "GPTME_TOOL_FORMAT",
]


class DockerGPTMeEnv(DockerExecutionEnv):
    """
    Docker-based execution environment that runs gptme itself inside Docker.

    This provides full isolation for evals by running the entire gptme CLI
    inside a Docker container, not just the verification commands.

    Benefits:
    - Full isolation: gptme and all tool execution contained
    - No host pollution: git config, file modifications stay in container
    - Security: Untrusted code execution sandboxed
    - Reproducibility: Consistent environment across runs

    Args:
        image: Docker image with gptme installed (default: gptme-eval:latest)
        working_dir: Working directory inside container (default: /workspace)
        host_dir: Host directory to mount (default: temp directory)
        log_dir: Directory to store gptme logs (default: host_dir/logs)
        timeout: Timeout for gptme execution in seconds (default: 300)
        env_passthrough: List of env vars to pass to container
    """

    def __init__(
        self,
        image: str = "gptme-eval:latest",
        working_dir: str = "/workspace",
        host_dir: Path | None = None,
        log_dir: Path | None = None,
        timeout: int = 300,
        env_passthrough: list[str] | None = None,
    ):
        super().__init__(image=image, working_dir=working_dir, host_dir=host_dir)
        self.log_dir = log_dir or (self.host_dir / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.env_passthrough = env_passthrough or DOCKER_ENV_PASSTHROUGH

    def _get_env_args(self) -> list[str]:
        """Get Docker -e arguments for environment variable passthrough."""
        env_args: list[str] = []
        for var in self.env_passthrough:
            value = os.environ.get(var)
            if value:
                env_args.extend(["-e", f"{var}={value}"])
        return env_args

    def start_container(self) -> None:
        """Start Docker container with volume mount and env passthrough."""
        try:
            cmd = [
                "docker",
                "run",
                "-d",
                "-e",
                "GPTME_LOGS_HOME=/app/logs",  # Tell gptme to use mounted logs dir
                "-v",
                f"{self.host_dir}:{self.working_dir}",
                "-v",
                f"{self.log_dir}:/app/logs",  # Mount logs directory
            ]
            # Add environment variable passthrough
            cmd.extend(self._get_env_args())
            cmd.extend(
                [
                    self.image,
                    "tail",
                    "-f",
                    "/dev/null",  # Keep container alive
                ]
            )

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            self.container_id = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to start Docker container with image '{self.image}'.\n"
            if "Unable to find image" in e.stderr or "No such image" in e.stderr:
                error_msg += "Docker image not found. Build it with: make build-docker"
            else:
                error_msg += f"Error: {e.stderr}"
            raise RuntimeError(error_msg) from e

    def run_gptme(
        self,
        prompt: str,
        model: str,
        tool_format: str = "markdown",
        tools: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> tuple[str, str, int]:
        """
        Run gptme CLI inside the Docker container.

        Args:
            prompt: The user prompt to process
            model: Model identifier (e.g., "openai/gpt-4o")
            tool_format: Tool format to use (default: "markdown")
            tools: List of tools to enable (default: all)
            system_prompt: Custom system prompt (default: None)

        Returns:
            Tuple of (stdout, stderr, exit_code)
        """
        if not self.container_id:
            self.start_container()

        assert self.container_id is not None

        # Build gptme CLI command
        # Use -n for non-interactive mode
        # All parameters are shell-escaped for safety
        cmd_parts = [
            "python",
            "-m",
            "gptme",
            "-n",  # Non-interactive
            "--model",
            shlex.quote(model),
            "--tool-format",
            shlex.quote(tool_format),
        ]

        # Add tools if specified (each tool name escaped)
        if tools:
            for tool in tools:
                cmd_parts.extend(["--tool", shlex.quote(tool)])

        # Add system prompt if specified (escaped)
        if system_prompt:
            cmd_parts.extend(["--system", shlex.quote(system_prompt)])

        # Add the user prompt (properly escaped using shlex)
        cmd_parts.append(shlex.quote(prompt))

        command = " ".join(cmd_parts)

        start = time.time()
        print("\n--- Start of gptme execution (Docker) ---")
        print(f"Model: {model}")
        print(f"Tool format: {tool_format}")
        print(
            f"Prompt: {prompt[:100]}..." if len(prompt) > 100 else f"Prompt: {prompt}"
        )

        # Execute gptme in Docker container
        p = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                "-w",
                self.working_dir,
                self.container_id,
                "/bin/bash",
                "-c",
                command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stdout_full, stderr_full = "", ""
        while p.poll() is None or p.stdout or p.stderr:
            assert p.stdout is not None
            assert p.stderr is not None
            stdout = p.stdout.readline()
            stderr = p.stderr.readline()
            if stdout:
                print(stdout, end="")
                stdout_full += stdout
            if stderr:
                print(stderr, end="")
                stderr_full += stderr
            if not stdout and not stderr and p.poll() is not None:
                break
            if time.time() - start > self.timeout:
                print(f"Timeout after {self.timeout}s!")
                p.kill()
                # Stop container to terminate gptme
                if self.container_id:
                    subprocess.run(
                        ["docker", "stop", self.container_id],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                break

        duration = time.time() - start
        print(f"--- Finished gptme execution (Docker) in {duration:.1f}s ---\n")

        return stdout_full, stderr_full, p.returncode if p.returncode is not None else 0

    def get_logs(self) -> dict[str, str]:
        """
        Retrieve gptme logs from the container.

        Returns:
            Dictionary mapping log file names to their contents
        """
        logs: dict[str, str] = {}
        for path in self.log_dir.glob("**/*.jsonl"):
            try:
                with open(path) as f:
                    logs[str(path.relative_to(self.log_dir))] = f.read()
            except (OSError, UnicodeDecodeError):
                pass
        return logs
