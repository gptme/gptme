import base64
import os
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
        os.chdir(self.working_dir)

        start = time.time()
        if not silent:
            print("\n--- Start of run ---")
        # while running, also print the stdout and stderr
        p = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.working_dir,
            text=True,
            shell=True,
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
