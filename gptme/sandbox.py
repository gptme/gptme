"""
Sandboxed execution support for gptme.

Wraps the bash subprocess launched by the shell tool in firejail or bubblewrap
to contain credential exfiltration, filesystem escape, and network misuse.
For the Python tool, a Docker or Wasmtime backend runs code in an isolated environment.

Environment variables:
    GPTME_SANDBOX: sandbox backend ("firejail" | "bwrap" | "docker" | "wasmtime" | "none", default "none")
    GPTME_SANDBOX_NET: "0" to disable network (default), "1" to allow
    GPTME_SANDBOX_RO_HOME: "1" to add read-only home bind (default "0")
    GPTME_SANDBOX_DOCKER_IMAGE: Docker image for python sandbox (default "python:3.12-slim")
    GPTME_SANDBOX_TIMEOUT: execution timeout in seconds (default 30)
    GPTME_SANDBOX_PYTHON_WASM: path to CPython WASI binary (default ~/.cache/gptme/python.wasm)

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

import importlib.util
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import urllib.request
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

    backend: str = "none"  # "firejail" | "bwrap" | "docker" | "wasmtime" | "none"
    workspace: Path = field(default_factory=Path.cwd)
    allow_network: bool = False
    ro_home: bool = False
    env_allowlist: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_ENV_ALLOWLIST
    )
    mask_secrets: bool = True
    docker_image: str = "python:3.12-slim"
    timeout: int = 30  # seconds; used by sandbox_exec_python / sandbox_exec_wasmtime
    python_wasm_path: Path | None = None  # wasmtime: CPython WASI binary path

    def __post_init__(self) -> None:
        if self.backend not in ("firejail", "bwrap", "docker", "wasmtime", "none"):
            raise ValueError(f"Unsupported sandbox backend: {self.backend!r}")

    @classmethod
    def from_env(cls, workspace: Path | None = None) -> SandboxConfig:
        """Build a SandboxConfig from environment variables."""
        backend = os.environ.get("GPTME_SANDBOX", "none").lower()

        allow_network = os.environ.get("GPTME_SANDBOX_NET", "0") == "1"
        ro_home = os.environ.get("GPTME_SANDBOX_RO_HOME", "0") == "1"
        docker_image = os.environ.get("GPTME_SANDBOX_DOCKER_IMAGE", "python:3.12-slim")
        # Only Docker and Wasmtime backends use a timeout. Do not let a stale or
        # malformed setting break shell or IPython execution.
        timeout = (
            int(os.environ.get("GPTME_SANDBOX_TIMEOUT", "30"))
            if backend in ("docker", "wasmtime")
            else 30
        )
        python_wasm_path = (
            Path(os.environ["GPTME_SANDBOX_PYTHON_WASM"])
            if "GPTME_SANDBOX_PYTHON_WASM" in os.environ
            else None
        )

        return cls(
            backend=backend,
            workspace=workspace or Path.cwd(),
            allow_network=allow_network,
            ro_home=ro_home,
            docker_image=docker_image,
            timeout=timeout,
            python_wasm_path=python_wasm_path,
        )

    @property
    def enabled(self) -> bool:
        return self.backend != "none"

    def check_available(self) -> tuple[bool, str]:
        """Return (available, message). Use before committing to this backend."""
        if self.backend == "none":
            return True, "no-op sandbox (passthrough)"
        if self.backend == "wasmtime":
            if importlib.util.find_spec("wasmtime") is None:
                return False, "wasmtime not installed. Run: pip install wasmtime"
            return True, "wasmtime Python bindings available"
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


def _parse_size(value: str) -> int:
    """Parse a human-readable size string into bytes.

    Accepts a plain integer (bytes) or an integer with a binary unit suffix
    (``K``/``M``/``G``/``T``, case-insensitive), e.g. ``"512M"`` → 536870912.
    """
    value = value.strip()
    if not value:
        raise ValueError("empty size")
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    suffix = value[-1].upper()
    factor = units.get(suffix, 1)
    number = value[:-1] if suffix in units else value
    try:
        result = int(number) * factor
    except ValueError as exc:
        raise ValueError(f"invalid size: {value!r}") from exc
    if result <= 0:
        raise ValueError(f"size must be positive: {value!r}")
    return result


def apply_memory_limit(cmd: list[str], limit: int | None) -> list[str]:
    """Cap the address space of a bash invocation via ``ulimit -v``.

    ``limit`` is in bytes (``None`` → no limit, returns ``cmd`` unchanged).
    POSIX-only: ``ulimit`` is a bash builtin, so callers skip this on Windows.

    Composes with the sandbox wrapper: apply it to the *inner* bash command
    before ``wrap_shell_cmd`` so firejail/bwrap run the limited shell inside
    the sandbox rather than limiting the sandbox launcher itself.

    Both forms are prefixed with ``env -u BASH_ENV`` so that bash starts with
    ``BASH_ENV`` unset in its process environment.  This prevents any user
    startup script referenced by ``$BASH_ENV`` from running *before* the
    ``ulimit -v`` command in the ``-c`` script, which would otherwise allow the
    startup code to execute outside the configured ceiling.

    Two forms are supported:
      - ``["bash"]``            → ``["env", "-u", "BASH_ENV", "bash", "-c",
                                    "ulimit -v N && BASH_ENV='' exec bash"]``
      - ``["bash", "-c", "..."]`` → ``["env", "-u", "BASH_ENV", "bash", "-c",
                                       "ulimit -v N && { ...; }"]``

    The persistent shell form additionally clears ``BASH_ENV`` inline before
    exec so the replacement shell also inherits a clean environment (belt and
    suspenders: env handles the outer shell, the inline assignment handles the
    exec'd shell).
    """
    if limit is None or not cmd:
        return cmd
    kib = max(
        1, (limit + 1023) // 1024
    )  # ulimit -v is in KiB; ceil so applied limit ≥ requested
    # Set both the hard and soft RLIMIT_AS so subprocesses cannot raise the
    # soft limit back to unlimited via `ulimit -v unlimited`.  The hard limit
    # is lowered first (best-effort; suppressed if it is already below kib, in
    # which case verify_memory_limit will have caught the unsatisfiable config
    # before we get here).  The soft limit is then set to the same value and
    # gates the user command via &&.
    prefix = f"ulimit -H -v {kib} 2>/dev/null; ulimit -v {kib} && "
    # Prepend `env -u BASH_ENV` so bash starts with BASH_ENV unset at the
    # process-environment level.  Bash sources BASH_ENV before executing any
    # -c script, so without this, startup code in $BASH_ENV would run before
    # ulimit is installed and could allocate memory outside the ceiling.
    env_prefix = ["env", "-u", "BASH_ENV"]
    if len(cmd) >= 3 and cmd[1] == "-c":
        # Wrap in a group so && gates the entire user script, not just its first
        # statement (shell parses `ulimit -v N && a; b` as `(ulimit && a); b`).
        # Use a newline before } so a trailing # comment in cmd[2] doesn't
        # comment out the closing brace.
        return [*env_prefix, cmd[0], cmd[1], prefix + "{ " + cmd[2] + "\n}", *cmd[3:]]
    # Persistent shell form: re-exec the shell so the limit applies to it and
    # every child it spawns.  Clear BASH_ENV inline before exec as well so the
    # replacement shell also has no BASH_ENV to source on startup.
    # Preserve all original arguments (e.g. --norc) in the exec'd shell so the
    # function honours its contract for any future caller that passes extra flags.
    exec_cmd = " ".join(shlex.quote(a) for a in cmd)
    return [*env_prefix, cmd[0], "-c", f"{prefix}BASH_ENV='' exec {exec_cmd}"]


def verify_memory_limit(limit: int) -> None:
    """Verify that both the hard and soft ``ulimit -v`` can be applied on this system.

    Sets the hard limit first (best-effort) then the soft limit so the check
    mirrors the actual prefix used in ``apply_memory_limit``.  Raises
    ``ValueError`` if either step fails — for example, if *limit* exceeds the
    current system hard limit and we cannot lower it.  Call this before
    spawning a shell so failures surface at the Python level — an informative
    error — rather than silently leaving the shell unrestricted.
    """
    kib = max(1, (limit + 1023) // 1024)
    result = subprocess.run(
        ["bash", "-c", f"ulimit -H -v {kib} 2>/dev/null; ulimit -v {kib}"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"GPTME_SHELL_MEMORY_LIMIT cannot be enforced: "
            f"'ulimit -v {kib}' exited {result.returncode}. "
            f"Check your system's ulimit hard limit or reduce the configured value."
        )


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


# ---------------------------------------------------------------------------
# Wasmtime Python sandbox
# ---------------------------------------------------------------------------

# CPython 3.12 WASI build from VMware Labs webassembly-language-runtimes.
# This is the only publicly available pre-built CPython WASI binary.
_PYTHON_WASM_URL = (
    "https://github.com/vmware-labs/webassembly-language-runtimes/releases/download/"
    "python%2F3.12.0%2B20231211-040d5a6/python-3.12.0.wasm"
)
_PYTHON_WASM_CACHE = Path.home() / ".cache" / "gptme" / "python.wasm"


def _ensure_python_wasm(path: Path | None = None) -> Path:
    """Return path to the cached CPython WASI binary, downloading if needed.

    Args:
        path: Override cache path. Defaults to ~/.cache/gptme/python.wasm.

    Returns:
        Path to the .wasm binary.

    Raises:
        RuntimeError: If download fails.
    """
    target = path or _PYTHON_WASM_CACHE
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading CPython WASI binary to %s …", target)
    tmp = target.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(_PYTHON_WASM_URL, tmp)
        tmp.rename(target)
        logger.info("CPython WASI binary downloaded (%s)", target)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download CPython WASI binary from {_PYTHON_WASM_URL}: {exc}\n"
            "Set GPTME_SANDBOX_PYTHON_WASM to a local .wasm path to skip the download."
        ) from exc
    return target


def sandbox_exec_wasmtime(
    config: SandboxConfig,
    code: str,
) -> tuple[str, str, int]:
    """Execute Python code in a Wasmtime WASI sandbox.

    Writes *code* to a temporary file, mounts the containing directory into the
    WASI environment, and runs CPython compiled to WASM/WASI via wasmtime-py.

    Args:
        config: SandboxConfig with backend=="wasmtime".
        code:   Python source to execute.

    Returns:
        (stdout, stderr, returncode).  On timeout, stderr contains a message
        and returncode is 1.

    Security boundaries:
        - No network access (WASI has no network capability by default).
        - No filesystem access beyond a private read-only script directory.
        - 256 MiB linear-memory cap via ``Store.set_limits``.
        - Execution timeout via Wasmtime epoch interruption.

    Caveats:
        - CPython WASI has slower startup than native Python (~2–5 s cold).
        - The full stdlib is not available (WASI port is experimental).
        - Imports that need native extensions (numpy, etc.) will fail.
    """
    if config.backend != "wasmtime":
        raise ValueError(
            f"sandbox_exec_wasmtime requires backend='wasmtime', got {config.backend!r}"
        )

    import wasmtime  # deferred: optional dependency

    wasm_path = _ensure_python_wasm(config.python_wasm_path)

    # A private directory is the capability boundary. Never preopen the shared
    # system temp directory: that would expose unrelated processes' temporary files.
    with tempfile.TemporaryDirectory(prefix="gptme_wasm_") as temp_dir:
        temp_path = Path(temp_dir)
        script_path = temp_path / "script.py"
        out_path = temp_path / "stdout"
        err_path = temp_path / "stderr"
        script_path.write_text(code)
        out_path.touch()
        err_path.touch()

        def _execute() -> tuple[str, str, int]:
            engine_config = wasmtime.Config()
            engine_config.epoch_interruption = True
            engine = wasmtime.Engine(engine_config)
            store = wasmtime.Store(engine)
            store.set_limits(memory_size=256 * 1024 * 1024)
            store.set_epoch_deadline(1)

            wasi_cfg = wasmtime.WasiConfig()
            wasi_cfg.argv = ["python", "/work/script.py"]
            wasi_cfg.preopen_dir(
                str(temp_path),
                "/work",
                dir_perms=wasmtime.DirPerms.READ_ONLY,
                file_perms=wasmtime.FilePerms.READ_ONLY,
            )
            # wasmtime-py exposes these as write-only properties, not methods.
            wasi_cfg.stdout_file = str(out_path)
            wasi_cfg.stderr_file = str(err_path)
            store.set_wasi(wasi_cfg)

            module = wasmtime.Module.from_file(engine, str(wasm_path))
            linker = wasmtime.Linker(engine)
            linker.define_wasi()
            instance = linker.instantiate(store, module)
            start = instance.exports(store)["_start"]
            if not isinstance(start, wasmtime.Func):
                raise RuntimeError("CPython WASI module exports non-function _start")

            timer = threading.Timer(config.timeout, engine.increment_epoch)
            timer.start()
            try:
                try:
                    start(store)
                    returncode = 0
                except wasmtime.ExitTrap as exc:
                    returncode = exc.code
                except wasmtime.Trap as exc:
                    if exc.trap_code is not wasmtime.TrapCode.INTERRUPT:
                        raise
                    logger.warning(
                        "Wasmtime sandbox: execution timed out after %ds",
                        config.timeout,
                    )
                    return "", f"Execution timed out after {config.timeout}s\n", 1
            finally:
                timer.cancel()

            stdout = out_path.read_text(errors="replace")
            stderr = err_path.read_text(errors="replace")
            return stdout, stderr, returncode

        try:
            return _execute()
        except Exception as exc:
            logger.error("Wasmtime sandbox error: %s", exc)
            return "", f"Wasmtime error: {exc}\n", 1
