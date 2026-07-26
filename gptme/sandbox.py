"""
Sandboxed execution support for gptme.

Wraps the bash subprocess launched by the shell tool in firejail or bubblewrap
to contain credential exfiltration, filesystem escape, and network misuse.
For the Python tool, a Docker backend runs code in an isolated container.

Environment variables:
    GPTME_SANDBOX: sandbox backend ("firejail" | "bwrap" | "docker" | "none", default "none")
    GPTME_SANDBOX_NET: "0" to disable network (default), "1" to allow
    GPTME_SANDBOX_RO_HOME: "1" to add read-only home bind (default "0")
    GPTME_SANDBOX_DOCKER_IMAGE: Docker image for python sandbox (default "python:3.12-slim")
    GPTME_SANDBOX_TIMEOUT: execution timeout in seconds (default 30)

The integration point in gptme/tools/shell.py is the subprocess.Popen call
that starts the persistent bash process. Caller passes a SandboxConfig and
calls wrap_shell_cmd() to get the (possibly sandboxed) command list.

Security scope (what this contains):
    - Credential exfiltration: env var masking + private tmpfs home
    - Filesystem escape: workspace-only bind, tmpfs home
    - Network misuse: --net=none (firejail) / --unshare-net (bwrap)
    - Resource exhaustion: firejail seccomp caps-drop; bwrap new-session

Out of scope:
    - Prompt injection → code execution (agent-level problem)
    - Determined attacker with prompt control (fundamental impossibility)
    - macOS/Windows (firejail + bwrap are Linux-only)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variables that are safe to pass into the sandbox.
# The API key vars are included so the agent can still call the LLM.
# Secrets NOT in this list are stripped before the subprocess is started.
_DEFAULT_ENV_ALLOWLIST = frozenset(
    [
        # Shell basics
        # NOTE: Provider API keys are intentionally excluded. LLM calls are
        # made in the parent Python process, not in the sandboxed bash shell.
        # Exposing credentials inside the sandbox (especially with
        # GPTME_SANDBOX_NET=1) would allow agent-generated commands to
        # exfiltrate them. Add keys to a custom env_allowlist if your workflow
        # needs agent scripts to call the LLM provider directly.
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "COLORTERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TZ",
        # Python
        "PYTHONUNBUFFERED",
        "PYTHONPATH",
        # Git (needed for code review tasks)
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        # gptme internal
        "GPTME_SHELL_TIMEOUT",
        "GPTME_SHELL_TRUNC_PRE_TOKENS",
        "GPTME_SHELL_TRUNC_POST_TOKENS",
    ]
)


@dataclass
class SandboxConfig:
    """Policy for sandboxed shell execution.

    Instantiate via SandboxConfig.from_env() to read from environment variables,
    or construct directly for programmatic control.
    """

    backend: str = "none"  # "firejail" | "bwrap" | "docker" | "none"
    workspace: Path = field(default_factory=Path.cwd)
    allow_network: bool = False
    ro_home: bool = False
    env_allowlist: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_ENV_ALLOWLIST
    )
    mask_secrets: bool = True
    docker_image: str = "python:3.12-slim"
    timeout: int = 30  # seconds; used by sandbox_exec_python

    def __post_init__(self) -> None:
        if self.backend not in ("firejail", "bwrap", "docker", "none"):
            raise ValueError(f"Unsupported sandbox backend: {self.backend!r}")

    @classmethod
    def from_env(cls, workspace: Path | None = None) -> SandboxConfig:
        """Build a SandboxConfig from environment variables."""
        backend = os.environ.get("GPTME_SANDBOX", "none").lower()

        allow_network = os.environ.get("GPTME_SANDBOX_NET", "0") == "1"
        ro_home = os.environ.get("GPTME_SANDBOX_RO_HOME", "0") == "1"
        docker_image = os.environ.get("GPTME_SANDBOX_DOCKER_IMAGE", "python:3.12-slim")
        # Docker is the only backend that uses this timeout. Do not let a stale or
        # malformed Docker-only setting break shell or IPython execution.
        timeout = (
            int(os.environ.get("GPTME_SANDBOX_TIMEOUT", "30"))
            if backend == "docker"
            else 30
        )

        return cls(
            backend=backend,
            workspace=workspace or Path.cwd(),
            allow_network=allow_network,
            ro_home=ro_home,
            docker_image=docker_image,
            timeout=timeout,
        )

    @property
    def enabled(self) -> bool:
        return self.backend != "none"

    def check_available(self) -> tuple[bool, str]:
        """Return (available, message). Use before committing to this backend."""
        if self.backend == "none":
            return True, "no-op sandbox (passthrough)"
        if self.backend == "docker":
            tool = "docker"
        else:
            tool = self.backend if self.backend != "bwrap" else "bwrap"
        if not shutil.which(tool):
            msg = f"{tool} not found. Install with: sudo apt install {tool}"
            return False, msg
        return True, f"{tool} found at {shutil.which(tool)}"


def wrap_shell_cmd(config: SandboxConfig, cmd: list[str]) -> list[str]:
    """Wrap cmd with the sandbox layer.

    For backend=="none", returns cmd unchanged.
    For firejail/bwrap, prepends the sandbox invocation.
    The caller is responsible for checking availability (check_available())
    before calling this function.
    """
    if not config.enabled:
        return cmd
    if config.backend == "firejail":
        return _firejail_cmd(config, cmd)
    if config.backend == "bwrap":
        return _bwrap_cmd(config, cmd)
    return cmd


def build_env(config: SandboxConfig) -> dict[str, str] | None:
    """Return sanitized environment dict, or None to inherit current env.

    Strips any env var not in the allowlist so secrets don't leak into the
    sandboxed process even if the filesystem/network sandbox is bypassed.
    """
    if not config.enabled or not config.mask_secrets:
        return None  # None → subprocess inherits os.environ unchanged
    return {k: v for k, v in os.environ.items() if k in config.env_allowlist}


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


def _firejail_cmd(config: SandboxConfig, inner: list[str]) -> list[str]:
    """Build firejail command wrapping inner.

    Policy:
        --private      : fresh tmpfs home (hides ~/.aws, ~/.ssh, ~/.gnupg, etc.)
        --whitelist=WS : bind the workspace read-write (agent's working dir)
        --noroot       : deny setuid/setgid binaries
        --seccomp      : default seccomp filter (blocks rare dangerous syscalls)
        --caps.drop=all: drop all capabilities
        --net=none     : no network (unless allow_network=True)
        --read-only=~  : optional read-only home overlay (ro_home=True)
    """
    workspace = str(config.workspace.resolve())
    cmd: list[str] = [
        "firejail",
        "--quiet",
        "--private",
        f"--whitelist={workspace}",
        "--noroot",
        "--seccomp",
        "--caps.drop=all",
    ]
    if config.ro_home:
        cmd.append(f"--read-only={Path.home()}")
    if not config.allow_network:
        cmd.append("--net=none")
    cmd.extend(["--", *inner])
    return cmd


def _bwrap_cmd(config: SandboxConfig, inner: list[str]) -> list[str]:
    """Build bubblewrap command wrapping inner.

    Policy (minimal, user-namespace-based):
        ro-bind /usr,/lib,...: share read-only OS libraries
        bind workspace       : read-write workspace
        tmpfs /home          : blank home (no credentials)
        tmpfs /tmp           : scratch space
        --new-session        : new session ID (prevents terminal injection)
        --die-with-parent    : clean shutdown if gptme exits
        --unshare-net        : no network (unless allow_network=True)
    """
    workspace = str(config.workspace.resolve())

    # Directories to bind read-only from the host OS
    ro_system_dirs = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/alternatives"]

    cmd: list[str] = ["bwrap"]

    for d in ro_system_dirs:
        if Path(d).exists():
            cmd.extend(["--ro-bind", d, d])

    # /etc: bind selectively to avoid leaking credentials
    # (resolv.conf needed for DNS; others are optional)
    for etc_file in [
        "/etc/resolv.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/os-release",
    ]:
        if Path(etc_file).exists():
            cmd.extend(["--ro-bind", etc_file, etc_file])

    # Scratch dirs
    cmd.extend(["--proc", "/proc"])
    cmd.extend(["--dev", "/dev"])
    cmd.extend(["--tmpfs", "/tmp"])
    # Blank home hides credentials (~/.ssh, ~/.aws, etc.).
    # Must come BEFORE the workspace bind so that when the workspace is under
    # /home (e.g. /home/user/project) the subsequent --bind overrides the tmpfs
    # at that specific path, making the workspace accessible.
    cmd.extend(["--tmpfs", "/home"])

    # Optional read-only home overlay (exposes home files read-only). Apply it
    # before the workspace bind so a workspace nested under home stays writable.
    if config.ro_home:
        home = str(Path.home())
        cmd.extend(["--ro-bind", home, home])

    # Workspace: read-write (after every home mount so it wins when nested there)
    cmd.extend(["--bind", workspace, workspace])

    # Isolate procfs from the credential-bearing gptme parent process.
    cmd.extend(["--unshare-pid", "--new-session", "--die-with-parent"])

    if not config.allow_network:
        cmd.append("--unshare-net")

    cmd.extend(["--", *inner])
    return cmd


# ---------------------------------------------------------------------------
# Docker Python sandbox
# ---------------------------------------------------------------------------


def sandbox_exec_python(
    config: SandboxConfig,
    code: str,
) -> tuple[str, str, int]:
    """Execute Python code in a Docker container sandbox.

    Writes *code* to a temporary file, mounts it read-only into the container,
    and runs ``python /tmp/script.py`` with resource limits applied.

    Args:
        config: SandboxConfig with backend=="docker".
        code:   Python source to execute.

    Returns:
        (stdout, stderr, returncode).  On timeout, stderr contains a message
        and returncode is 1.

    Security boundaries:
        - No network (unless config.allow_network).
        - 256 MiB memory cap, no swap (OOM → container exit 137).
        - 512 PID limit (fork-bomb mitigation).
        - All Linux capabilities dropped (--cap-drop=ALL).
        - No privilege escalation (--security-opt=no-new-privileges).
        - Script mounted read-only; workspace bind-mounted read-write.
        - Fresh, ephemeral filesystem (no host credentials visible).
    """
    if config.backend != "docker":
        raise ValueError(
            f"sandbox_exec_python requires backend='docker', got {config.backend!r}"
        )

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="gptme_sandbox_"
    ) as f:
        f.write(code)
        script_path = Path(f.name)
    # NamedTemporaryFile creates mode 0600. Once all container capabilities are
    # dropped, container root cannot bypass that host ownership check on a bind
    # mount. The script contains only the caller-provided code and is mounted
    # read-only, so make it world-readable before starting the container.
    script_path.chmod(0o644)

    try:
        workspace = str(config.workspace.resolve())
        # Named container so we can force-stop it via the Docker daemon on timeout.
        container_name = f"gptme-sandbox-{uuid.uuid4().hex[:8]}"

        cmd: list[str] = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--memory=256m",
            "--memory-swap=256m",  # disallow swap (OOM kills instead of thrashing)
            "--pids-limit=512",
            "--cap-drop=ALL",  # drop all Linux capabilities (no CAP_NET_RAW etc.)
            "--security-opt=no-new-privileges",  # prevent privilege escalation
            # Mount script as read-only inside the container
            "-v",
            f"{script_path}:/tmp/script.py:ro",
            # Workspace read-write so generated files land in expected places
            "-v",
            f"{workspace}:{workspace}",
            "-w",
            workspace,
        ]

        if not config.allow_network:
            cmd.append("--network=none")

        cmd += [config.docker_image, "python", "/tmp/script.py"]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=config.timeout)
            return stdout, stderr, proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # collect remaining output and release resources
            # Force-stop the container via the Docker daemon: killing the `docker
            # run` client process does not stop the daemon-managed container.
            subprocess.run(
                ["docker", "kill", container_name],
                capture_output=True,
                check=False,
            )
            logger.warning(
                "Docker Python sandbox: execution timed out after %ds", config.timeout
            )
            return "", f"Execution timed out after {config.timeout}s\n", 1
    finally:
        script_path.unlink(missing_ok=True)
