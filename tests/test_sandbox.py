"""Tests for gptme.sandbox — sandbox wrapper module (Idea #834)."""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.sandbox import (
    _DEFAULT_ENV_ALLOWLIST,
    _PYTHON_WASM_CACHE,
    SandboxConfig,
    _bwrap_cmd,
    _ensure_python_wasm,
    _firejail_cmd,
    _parse_size,
    apply_memory_limit,
    build_env,
    sandbox_exec_python,
    sandbox_exec_wasmtime,
    verify_memory_limit,
    wrap_shell_cmd,
)

# ---------------------------------------------------------------------------
# SandboxConfig
# ---------------------------------------------------------------------------


class TestSandboxConfigFromEnv:
    def test_default_is_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GPTME_SANDBOX", None)
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "none"
        assert not cfg.enabled

    def test_firejail_backend(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "firejail"}):
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "firejail"
        assert cfg.enabled

    def test_bwrap_backend(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "bwrap"}):
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "bwrap"
        assert cfg.enabled

    def test_unknown_backend_fails_closed(self):
        with (
            patch.dict(os.environ, {"GPTME_SANDBOX": "nsjail"}),
            pytest.raises(ValueError, match="Unsupported sandbox backend"),
        ):
            SandboxConfig.from_env()

    def test_programmatic_unknown_backend_fails_closed(self):
        with pytest.raises(ValueError, match="Unsupported sandbox backend"):
            SandboxConfig(backend="nsjail")

    def test_network_disabled_by_default(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "firejail"}):
            cfg = SandboxConfig.from_env()
        assert not cfg.allow_network

    def test_network_can_be_enabled(self):
        with patch.dict(
            os.environ, {"GPTME_SANDBOX": "firejail", "GPTME_SANDBOX_NET": "1"}
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.allow_network

    def test_ro_home_disabled_by_default(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "bwrap"}):
            cfg = SandboxConfig.from_env()
        assert not cfg.ro_home

    def test_ro_home_can_be_enabled(self):
        with patch.dict(
            os.environ, {"GPTME_SANDBOX": "bwrap", "GPTME_SANDBOX_RO_HOME": "1"}
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.ro_home


class TestSandboxConfigCheckAvailable:
    def test_none_backend_always_available(self):
        cfg = SandboxConfig(backend="none")
        ok, _ = cfg.check_available()
        assert ok

    def test_missing_backend_not_available(self):
        cfg = SandboxConfig(backend="firejail")
        with patch("shutil.which", return_value=None):
            ok, msg = cfg.check_available()
        assert not ok
        assert "firejail" in msg

    def test_present_backend_available(self):
        cfg = SandboxConfig(backend="firejail")
        with patch("shutil.which", return_value="/usr/bin/firejail"):
            ok, _ = cfg.check_available()
        assert ok


# ---------------------------------------------------------------------------
# wrap_shell_cmd
# ---------------------------------------------------------------------------


class TestWrapShellCmd:
    def test_none_backend_passthrough(self):
        cfg = SandboxConfig(backend="none")
        cmd = ["bash"]
        assert wrap_shell_cmd(cfg, cmd) == cmd

    def test_firejail_backend_wraps_cmd(self):
        cfg = SandboxConfig(backend="firejail", workspace=Path("/workspace"))
        with patch("shutil.which", return_value="/usr/bin/firejail"):
            wrapped = wrap_shell_cmd(cfg, ["bash"])
        assert wrapped[0] == "firejail"
        assert "bash" in wrapped
        assert "--private" in wrapped

    def test_bwrap_backend_wraps_cmd(self):
        cfg = SandboxConfig(backend="bwrap", workspace=Path("/workspace"))
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            wrapped = wrap_shell_cmd(cfg, ["bash"])
        assert wrapped[0] == "bwrap"
        assert "bash" in wrapped

    def test_inner_command_appears_after_separator(self):
        cfg = SandboxConfig(backend="firejail", workspace=Path("/workspace"))
        with patch("shutil.which", return_value="/usr/bin/firejail"):
            wrapped = wrap_shell_cmd(cfg, ["bash", "-c", "echo hi"])
        sep_idx = wrapped.index("--")
        assert wrapped[sep_idx + 1 :] == ["bash", "-c", "echo hi"]


# ---------------------------------------------------------------------------
# firejail command builder
# ---------------------------------------------------------------------------


class TestFirejailCmd:
    def _build(self, **kwargs):
        workspace = kwargs.pop("workspace", Path("/project"))
        cfg = SandboxConfig(backend="firejail", workspace=workspace, **kwargs)
        return _firejail_cmd(cfg, ["bash"])

    def test_starts_with_firejail(self):
        cmd = self._build()
        assert cmd[0] == "firejail"

    def test_private_flag_present(self):
        assert "--private" in self._build()

    def test_noroot_flag_present(self):
        assert "--noroot" in self._build()

    def test_seccomp_flag_present(self):
        assert "--seccomp" in self._build()

    def test_net_none_when_network_disabled(self):
        cmd = self._build(allow_network=False)
        assert "--net=none" in cmd

    def test_no_net_none_when_network_enabled(self):
        cmd = self._build(allow_network=True)
        assert "--net=none" not in cmd

    def test_whitelist_includes_workspace(self):
        cmd = self._build(workspace=Path("/my/workspace"))
        assert "--whitelist=/my/workspace" in cmd

    def test_ro_home_flag_absent_by_default(self):
        cmd = self._build(ro_home=False)
        ro_flags = [f for f in cmd if f.startswith("--read-only=")]
        assert not ro_flags

    def test_ro_home_flag_present_when_enabled(self):
        cmd = self._build(ro_home=True)
        ro_flags = [f for f in cmd if f.startswith("--read-only=")]
        assert len(ro_flags) == 1

    def test_separator_before_inner_cmd(self):
        cmd = self._build()
        assert "--" in cmd
        assert cmd[-1] == "bash"


# ---------------------------------------------------------------------------
# bubblewrap command builder
# ---------------------------------------------------------------------------


class TestBwrapCmd:
    def _build(self, **kwargs):
        workspace = kwargs.pop("workspace", Path("/project"))
        cfg = SandboxConfig(backend="bwrap", workspace=workspace, **kwargs)
        return _bwrap_cmd(cfg, ["bash"])

    def test_starts_with_bwrap(self):
        assert self._build()[0] == "bwrap"

    def test_workspace_bind_present(self):
        cmd = self._build(workspace=Path("/project"))
        # Should have --bind /project /project
        try:
            idx = cmd.index("--bind")
        except ValueError:
            pytest.fail("--bind not found in bwrap command")
        assert cmd[idx + 1] == "/project"
        assert cmd[idx + 2] == "/project"

    def test_tmpfs_home_present(self):
        cmd = self._build()
        # --tmpfs /home should be present (blank home = no credentials)
        tmpfs_targets = []
        for i, arg in enumerate(cmd):
            if arg == "--tmpfs" and i + 1 < len(cmd):
                tmpfs_targets.append(cmd[i + 1])
        assert "/home" in tmpfs_targets

    def test_unshare_net_when_network_disabled(self):
        assert "--unshare-net" in self._build(allow_network=False)

    def test_no_unshare_net_when_network_enabled(self):
        assert "--unshare-net" not in self._build(allow_network=True)

    def test_die_with_parent_present(self):
        assert "--die-with-parent" in self._build()

    def test_new_session_present(self):
        assert "--new-session" in self._build()

    def test_pid_namespace_isolated(self):
        """The sandbox must not see the credential-bearing parent via /proc."""
        assert "--unshare-pid" in self._build()

    def test_separator_before_inner_cmd(self):
        cmd = self._build()
        assert "--" in cmd
        assert cmd[-1] == "bash"

    def test_workspace_bind_after_home_tmpfs(self):
        """Workspace bind must come after --tmpfs /home so it wins when
        the workspace is under /home (e.g. /home/user/project)."""
        cmd = self._build(workspace=Path("/home/user/project"))
        tmpfs_home_idx = next(
            (i for i, a in enumerate(cmd) if a == "--tmpfs" and cmd[i + 1] == "/home"),
            None,
        )
        workspace_bind_idx = next(
            (
                i
                for i, a in enumerate(cmd)
                if a == "--bind" and cmd[i + 1] == "/home/user/project"
            ),
            None,
        )
        assert tmpfs_home_idx is not None, "--tmpfs /home not found in bwrap command"
        assert workspace_bind_idx is not None, "--bind /home/user/project not found"
        assert workspace_bind_idx > tmpfs_home_idx, (
            "workspace --bind must appear after --tmpfs /home so it overrides"
            " the blank home mount when workspace is under /home"
        )

    def test_workspace_bind_after_read_only_home_overlay(self):
        """A read-only home overlay must not hide a nested writable workspace."""
        home = Path.home()
        workspace = home / "project"
        cmd = self._build(workspace=workspace, ro_home=True)
        home_bind_idx = next(
            (
                i
                for i, arg in enumerate(cmd)
                if arg == "--ro-bind" and cmd[i + 1 : i + 3] == [str(home), str(home)]
            ),
            None,
        )
        workspace_bind_idx = next(
            (
                i
                for i, arg in enumerate(cmd)
                if arg == "--bind"
                and cmd[i + 1 : i + 3] == [str(workspace), str(workspace)]
            ),
            None,
        )
        assert home_bind_idx is not None, "read-only home bind not found"
        assert workspace_bind_idx is not None, "workspace bind not found"
        assert workspace_bind_idx > home_bind_idx, (
            "workspace --bind must appear after the read-only home overlay"
        )


# ---------------------------------------------------------------------------
# Environment filtering
# ---------------------------------------------------------------------------


class TestBuildEnv:
    def test_none_when_sandbox_disabled(self):
        cfg = SandboxConfig(backend="none")
        assert build_env(cfg) is None

    def test_returns_dict_when_sandbox_enabled(self):
        cfg = SandboxConfig(backend="firejail")
        with patch.dict(os.environ, {"MY_SECRET": "s3cr3t", "HOME": "/home/user"}):
            env = build_env(cfg)
        assert env is not None
        assert isinstance(env, dict)

    def test_strips_non_allowlisted_vars(self):
        cfg = SandboxConfig(backend="firejail")
        with patch.dict(os.environ, {"MY_SECRET": "s3cr3t", "MY_AWS_KEY": "key123"}):
            env = build_env(cfg)
        assert "MY_SECRET" not in (env or {})
        assert "MY_AWS_KEY" not in (env or {})

    def test_strips_provider_api_keys(self):
        """Provider API keys must NOT be passed into the sandboxed shell.
        LLM calls are made by the parent Python process, not by bash commands.
        Exposing them (especially with GPTME_SANDBOX_NET=1) enables exfiltration."""
        cfg = SandboxConfig(backend="firejail")
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-test",
                "ANTHROPIC_API_KEY": "ant-test",
                "OPENROUTER_API_KEY": "or-test",
            },
        ):
            env = build_env(cfg)
        assert env is not None
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENROUTER_API_KEY" not in env

    def test_mask_secrets_false_returns_none(self):
        cfg = SandboxConfig(backend="firejail", mask_secrets=False)
        assert build_env(cfg) is None

    def test_allowlist_covers_shell_basics(self):
        required = {"HOME", "USER", "PATH", "TERM"}
        assert required.issubset(_DEFAULT_ENV_ALLOWLIST)


# ---------------------------------------------------------------------------
# Docker backend — config tests (no Docker daemon required)
# ---------------------------------------------------------------------------


class TestDockerBackendConfig:
    def test_docker_backend_accepted(self):
        cfg = SandboxConfig(backend="docker")
        assert cfg.backend == "docker"
        assert cfg.enabled

    def test_docker_backend_from_env(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "docker"}):
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "docker"

    def test_docker_image_default(self):
        cfg = SandboxConfig(backend="docker")
        assert cfg.docker_image == "python:3.12-slim"

    def test_docker_image_from_env(self):
        with patch.dict(
            os.environ,
            {"GPTME_SANDBOX": "docker", "GPTME_SANDBOX_DOCKER_IMAGE": "python:3.11"},
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.docker_image == "python:3.11"

    def test_timeout_default(self):
        cfg = SandboxConfig(backend="docker")
        assert cfg.timeout == 30

    def test_timeout_from_env(self):
        with patch.dict(
            os.environ, {"GPTME_SANDBOX": "docker", "GPTME_SANDBOX_TIMEOUT": "60"}
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.timeout == 60

    def test_malformed_docker_timeout_does_not_break_other_backends(self):
        with patch.dict(
            os.environ,
            {"GPTME_SANDBOX": "none", "GPTME_SANDBOX_TIMEOUT": "invalid"},
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "none"
        assert cfg.timeout == 30

    def test_check_available_when_docker_missing(self):
        cfg = SandboxConfig(backend="docker")
        with patch("shutil.which", return_value=None):
            ok, msg = cfg.check_available()
        assert not ok
        assert "docker" in msg

    def test_check_available_when_docker_present(self):
        cfg = SandboxConfig(backend="docker")
        with patch("shutil.which", return_value="/usr/bin/docker"):
            ok, _ = cfg.check_available()
        assert ok

    def test_exec_python_requires_docker_backend(self):
        cfg = SandboxConfig(backend="none")
        with pytest.raises(ValueError, match="backend='docker'"):
            sandbox_exec_python(cfg, "print('hi')")


# ---------------------------------------------------------------------------
# Docker backend — command-shape tests (mock subprocess.run)
# ---------------------------------------------------------------------------


class TestDockerExecPythonCommandShape:
    """Verify the docker run invocation shape without a live Docker daemon."""

    def _run(self, code: str, **cfg_kwargs) -> MagicMock:
        cfg = SandboxConfig(
            backend="docker", workspace=Path("/workspace"), **cfg_kwargs
        )
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("hello\n", "")
        mock_proc.returncode = 0
        with patch("gptme.sandbox.subprocess.Popen", return_value=mock_proc) as mock:
            sandbox_exec_python(cfg, code)
            return mock

    def test_docker_run_is_first_arg(self):
        mock = self._run("print('hi')")
        cmd = mock.call_args[0][0]
        assert cmd[:2] == ["docker", "run"]

    def test_rm_flag_present(self):
        cmd = self._run("x=1").call_args[0][0]
        assert "--rm" in cmd

    def test_memory_limit_present(self):
        cmd = self._run("x=1").call_args[0][0]
        assert "--memory=256m" in cmd

    def test_pids_limit_present(self):
        cmd = self._run("x=1").call_args[0][0]
        assert "--pids-limit=512" in cmd

    def test_cap_drop_all_present(self):
        """All Linux capabilities must be dropped to prevent raw sockets etc."""
        cmd = self._run("x=1").call_args[0][0]
        assert "--cap-drop=ALL" in cmd

    def test_no_new_privileges_present(self):
        """Privilege escalation must be blocked."""
        cmd = self._run("x=1").call_args[0][0]
        assert "--security-opt=no-new-privileges" in cmd

    def test_network_none_by_default(self):
        cmd = self._run("x=1").call_args[0][0]
        assert "--network=none" in cmd

    def test_network_not_disabled_when_allowed(self):
        cmd = self._run("x=1", allow_network=True).call_args[0][0]
        assert "--network=none" not in cmd

    def test_workspace_mounted(self):
        mock = self._run("x=1")
        cmd = mock.call_args[0][0]
        # -v /workspace:/workspace should appear
        v_pairs = [
            cmd[i + 1] for i, a in enumerate(cmd) if a == "-v" and i + 1 < len(cmd)
        ]
        assert any("/workspace:/workspace" in p for p in v_pairs)

    def test_script_mounted_readonly(self):
        mock = self._run("x=1")
        cmd = mock.call_args[0][0]
        v_pairs = [
            cmd[i + 1] for i, a in enumerate(cmd) if a == "-v" and i + 1 < len(cmd)
        ]
        assert any(":ro" in p for p in v_pairs)

    def test_script_is_readable_without_dac_override(self):
        """Capability-free container root must be able to read the bind mount."""
        observed_mode = None

        def inspect_mode(cmd, **kwargs):
            nonlocal observed_mode
            script_mount = next(
                cmd[i + 1]
                for i, arg in enumerate(cmd)
                if arg == "-v" and cmd[i + 1].endswith(":/tmp/script.py:ro")
            )
            observed_mode = Path(script_mount.split(":", 1)[0]).stat().st_mode & 0o777
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            return mock_proc

        cfg = SandboxConfig(backend="docker", workspace=Path("/workspace"))
        with patch("gptme.sandbox.subprocess.Popen", side_effect=inspect_mode):
            sandbox_exec_python(cfg, "x=1")

        assert observed_mode == 0o644

    def test_working_dir_set_to_workspace(self):
        mock = self._run("x=1")
        cmd = mock.call_args[0][0]
        w_idx = next((i for i, a in enumerate(cmd) if a == "-w"), None)
        assert w_idx is not None
        assert cmd[w_idx + 1] == "/workspace"

    def test_timeout_passed_to_communicate(self):
        cfg = SandboxConfig(backend="docker", workspace=Path("/ws"), timeout=15)
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        with patch("gptme.sandbox.subprocess.Popen", return_value=mock_proc):
            sandbox_exec_python(cfg, "pass")
        mock_proc.communicate.assert_called_once_with(timeout=15)

    def test_timeout_kills_container_and_returns_error(self):
        import subprocess

        cfg = SandboxConfig(backend="docker", workspace=Path("/ws"), timeout=1)
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="docker", timeout=1),
            ("", ""),  # cleanup call after proc.kill()
        ]
        with (
            patch("gptme.sandbox.subprocess.Popen", return_value=mock_proc),
            patch("gptme.sandbox.subprocess.run") as mock_run,  # docker kill
        ):
            stdout, stderr, rc = sandbox_exec_python(cfg, "import time; time.sleep(99)")
        assert rc == 1
        assert "timed out" in stderr
        mock_proc.kill.assert_called_once()
        # docker kill was called to stop the container
        kill_call = mock_run.call_args
        assert kill_call is not None
        assert "kill" in kill_call[0][0]


# ---------------------------------------------------------------------------
# Docker backend — integration tests (require live Docker daemon)
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None
requires_docker = pytest.mark.skipif(
    not docker_available, reason="docker not available"
)


@requires_docker
class TestDockerExecPythonIntegration:
    """Smoke tests that need a running Docker daemon and the python:3.12-slim image."""

    def _cfg(self, **kwargs) -> SandboxConfig:
        return SandboxConfig(
            backend="docker",
            workspace=Path.cwd(),
            timeout=30,
            **kwargs,
        )

    def test_basic_print(self):
        stdout, stderr, rc = sandbox_exec_python(self._cfg(), "print('hello sandbox')")
        assert rc == 0
        assert "hello sandbox" in stdout

    def test_arithmetic(self):
        stdout, stderr, rc = sandbox_exec_python(self._cfg(), "print(2 + 2)")
        assert rc == 0
        assert "4" in stdout

    def test_syntax_error_surfaces_in_stderr(self):
        stdout, stderr, rc = sandbox_exec_python(self._cfg(), "def foo(:\n  pass")
        assert rc != 0
        assert stderr  # Python prints SyntaxError to stderr

    def test_runtime_exception_surfaces(self):
        stdout, stderr, rc = sandbox_exec_python(
            self._cfg(), "raise ValueError('boom')"
        )
        assert rc != 0
        assert "ValueError" in stderr

    def test_no_network_by_default(self):
        """Container should not be able to open network connections."""
        code = (
            "import socket\n"
            "try:\n"
            "    socket.setdefaulttimeout(2)\n"
            "    socket.create_connection(('1.1.1.1', 80))\n"
            "    print('NETWORK_REACHABLE')\n"
            "except OSError:\n"
            "    print('NETWORK_BLOCKED')\n"
        )
        stdout, stderr, rc = sandbox_exec_python(self._cfg(), code)
        assert "NETWORK_BLOCKED" in stdout, (
            "Expected network to be blocked in sandbox, but got: " + stdout
        )

    def test_home_credentials_not_visible(self):
        """The container's home should be a blank tmpfs, not the host's ~/.ssh."""
        code = (
            "import os\n"
            "home = os.path.expanduser('~')\n"
            "has_ssh = os.path.exists(os.path.join(home, '.ssh'))\n"
            "has_aws = os.path.exists(os.path.join(home, '.aws'))\n"
            "print('ssh:', has_ssh, 'aws:', has_aws)\n"
        )
        stdout, stderr, rc = sandbox_exec_python(self._cfg(), code)
        assert rc == 0
        assert "ssh: False" in stdout
        assert "aws: False" in stdout


# ---------------------------------------------------------------------------
# Wasmtime backend — config tests (no wasmtime install required)
# ---------------------------------------------------------------------------


class TestWasmtimeBackendConfig:
    def test_wasmtime_backend_accepted(self):
        cfg = SandboxConfig(backend="wasmtime")
        assert cfg.backend == "wasmtime"
        assert cfg.enabled

    def test_wasmtime_backend_from_env(self):
        with patch.dict(os.environ, {"GPTME_SANDBOX": "wasmtime"}):
            cfg = SandboxConfig.from_env()
        assert cfg.backend == "wasmtime"

    def test_timeout_from_env(self):
        with patch.dict(
            os.environ, {"GPTME_SANDBOX": "wasmtime", "GPTME_SANDBOX_TIMEOUT": "60"}
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.timeout == 60

    def test_python_wasm_path_default_is_none(self):
        cfg = SandboxConfig(backend="wasmtime")
        assert cfg.python_wasm_path is None

    def test_python_wasm_path_from_env(self, tmp_path):
        wasm_file = tmp_path / "python.wasm"
        with patch.dict(
            os.environ,
            {
                "GPTME_SANDBOX": "wasmtime",
                "GPTME_SANDBOX_PYTHON_WASM": str(wasm_file),
            },
        ):
            cfg = SandboxConfig.from_env()
        assert cfg.python_wasm_path == wasm_file

    def test_check_available_when_wasmtime_missing(self):
        cfg = SandboxConfig(backend="wasmtime")
        with patch("gptme.sandbox.importlib.util.find_spec", return_value=None):
            ok, msg = cfg.check_available()
        assert not ok
        assert "wasmtime" in msg

    def test_check_available_when_wasmtime_present(self):
        cfg = SandboxConfig(backend="wasmtime")
        with patch("gptme.sandbox.importlib.util.find_spec", return_value=object()):
            ok, _ = cfg.check_available()
        assert ok

    def test_exec_wasmtime_wrong_backend_raises(self):
        cfg = SandboxConfig(backend="none")
        with pytest.raises(ValueError, match="backend='wasmtime'"):
            sandbox_exec_wasmtime(cfg, "pass")

    def test_default_cache_path(self):
        assert Path.home() / ".cache" / "gptme" / "python.wasm" == _PYTHON_WASM_CACHE


# ---------------------------------------------------------------------------
# _ensure_python_wasm
# ---------------------------------------------------------------------------


class TestEnsurePythonWasm:
    def test_returns_existing_path_without_download(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        with patch("gptme.sandbox.urllib.request.urlretrieve") as mock_dl:
            result = _ensure_python_wasm(wasm)
        assert result == wasm
        mock_dl.assert_not_called()

    def test_downloads_when_missing(self, tmp_path):
        wasm = tmp_path / "python.wasm"

        def fake_download(url, dest):
            Path(dest).write_bytes(b"\x00asm")

        with patch(
            "gptme.sandbox.urllib.request.urlretrieve", side_effect=fake_download
        ):
            result = _ensure_python_wasm(wasm)
        assert result == wasm
        assert wasm.exists()

    def test_raises_on_download_failure(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        with (
            patch(
                "gptme.sandbox.urllib.request.urlretrieve",
                side_effect=OSError("network error"),
            ),
            pytest.raises(RuntimeError, match="Failed to download"),
        ):
            _ensure_python_wasm(wasm)

    def test_cleans_up_tmp_on_failure(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        with patch(
            "gptme.sandbox.urllib.request.urlretrieve",
            side_effect=OSError("network error"),
        ):
            try:
                _ensure_python_wasm(wasm)
            except RuntimeError:
                pass
        # .tmp leftover must not exist
        assert not any(tmp_path.glob("*.tmp"))

    def test_uses_default_cache_path(self, monkeypatch, tmp_path):
        default_path = tmp_path / "python.wasm"
        default_path.write_bytes(b"\x00asm")
        monkeypatch.setattr("gptme.sandbox._PYTHON_WASM_CACHE", default_path)
        result = _ensure_python_wasm()  # no explicit path
        assert result == default_path


# ---------------------------------------------------------------------------
# Wasmtime exec — unit tests (mocked wasmtime)
# ---------------------------------------------------------------------------


class TestWasmtimeExecUnit:
    """Tests for sandbox_exec_wasmtime() without a real CPython WASI binary."""

    def _cfg(self, wasm_path: Path, **kwargs) -> SandboxConfig:
        return SandboxConfig(
            backend="wasmtime",
            workspace=Path("/ws"),
            python_wasm_path=wasm_path,
            **kwargs,
        )

    def _mock_wasmtime(self, start_fn=None):
        """Build the minimal wasmtime surface used by the executor."""
        mock_wt = MagicMock()
        mock_wt.ExitTrap = type("ExitTrap", (Exception,), {})
        mock_wt.Trap = type("Trap", (Exception,), {})
        mock_wt.TrapCode.INTERRUPT = "interrupt"
        mock_wt.DirPerms.READ_ONLY = "dir-read-only"
        mock_wt.FilePerms.READ_ONLY = "file-read-only"
        mock_wt.WasiConfig.return_value = MagicMock()
        mock_start = start_fn or MagicMock()
        mock_wt.Func = type(mock_start)
        mock_instance = MagicMock()
        mock_instance.exports.return_value = {"_start": mock_start}
        mock_wt.Linker.return_value.instantiate.return_value = mock_instance
        return mock_wt

    def test_wrong_backend_raises(self):
        cfg = SandboxConfig(backend="none")
        with pytest.raises(ValueError, match="backend='wasmtime'"):
            sandbox_exec_wasmtime(cfg, "pass")

    def test_exit_code_zero_on_clean_run(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        mock_wt = self._mock_wasmtime()

        with patch.dict("sys.modules", {"wasmtime": mock_wt}):
            stdout, stderr, rc = sandbox_exec_wasmtime(self._cfg(wasm), "pass")

        assert (stdout, stderr, rc) == ("", "", 0)
        assert mock_wt.Config.return_value.epoch_interruption is True
        mock_wt.Store.return_value.set_limits.assert_called_once_with(
            memory_size=256 * 1024 * 1024
        )
        mock_wt.Store.return_value.set_epoch_deadline.assert_called_once_with(1)
        wasi_cfg = mock_wt.WasiConfig.return_value
        assert Path(wasi_cfg.stdout_file).name == "stdout"
        assert Path(wasi_cfg.stderr_file).name == "stderr"

    def test_preopens_private_directory_read_only(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        mock_wt = self._mock_wasmtime()

        with patch.dict("sys.modules", {"wasmtime": mock_wt}):
            sandbox_exec_wasmtime(self._cfg(wasm), "pass")

        args, kwargs = mock_wt.WasiConfig.return_value.preopen_dir.call_args
        assert args[1] == "/work"
        assert Path(args[0]).name.startswith("gptme_wasm_")
        assert Path(args[0]) != Path(tempfile.gettempdir())
        assert kwargs == {
            "dir_perms": mock_wt.DirPerms.READ_ONLY,
            "file_perms": mock_wt.FilePerms.READ_ONLY,
        }

    def test_timeout_interrupts_guest_and_returns_error(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        mock_wt = self._mock_wasmtime()
        timer = MagicMock()
        timeout_trap = mock_wt.Trap("interrupt")
        timeout_trap.trap_code = mock_wt.TrapCode.INTERRUPT
        start = MagicMock(side_effect=timeout_trap)
        mock_wt.Func = type(start)
        mock_wt.Linker.return_value.instantiate.return_value.exports.return_value = {
            "_start": start
        }

        with (
            patch("gptme.sandbox.threading.Timer", return_value=timer),
            patch.dict("sys.modules", {"wasmtime": mock_wt}),
        ):
            stdout, stderr, rc = sandbox_exec_wasmtime(self._cfg(wasm), "pass")

        assert (stdout, rc) == ("", 1)
        assert "timed out" in stderr
        timer.start.assert_called_once_with()
        timer.cancel.assert_called_once_with()

    def test_non_timeout_trap_returns_error(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        mock_wt = self._mock_wasmtime()
        timer = MagicMock()
        guest_trap = mock_wt.Trap("guest trap")
        guest_trap.trap_code = "unreachable"
        start = MagicMock(side_effect=guest_trap)
        mock_wt.Func = type(start)
        mock_wt.Linker.return_value.instantiate.return_value.exports.return_value = {
            "_start": start
        }

        with (
            patch("gptme.sandbox.threading.Timer", return_value=timer),
            patch.dict("sys.modules", {"wasmtime": mock_wt}),
        ):
            stdout, stderr, rc = sandbox_exec_wasmtime(self._cfg(wasm), "pass")

        assert (stdout, rc) == ("", 1)
        assert "guest trap" in stderr
        assert "timed out" not in stderr
        timer.cancel.assert_called_once_with()

    def test_engine_error_returns_error(self, tmp_path):
        wasm = tmp_path / "python.wasm"
        wasm.write_bytes(b"\x00asm")
        mock_wt = self._mock_wasmtime()
        mock_wt.Engine.side_effect = RuntimeError("wasmtime engine init failed")

        with patch.dict("sys.modules", {"wasmtime": mock_wt}):
            stdout, stderr, rc = sandbox_exec_wasmtime(self._cfg(wasm), "pass")

        assert (stdout, rc) == ("", 1)
        assert "Wasmtime error" in stderr


# ---------------------------------------------------------------------------
# _parse_size
# ---------------------------------------------------------------------------


class TestParseSize:
    def test_plain_bytes(self):
        assert _parse_size("1024") == 1024

    def test_kilobytes_suffix(self):
        assert _parse_size("4K") == 4 * 1024

    def test_megabytes_suffix(self):
        assert _parse_size("512M") == 512 * 1024**2

    def test_gigabytes_suffix_case_insensitive(self):
        assert _parse_size("1g") == 1024**3

    def test_terabytes_suffix(self):
        assert _parse_size("2T") == 2 * 1024**4

    def test_strips_whitespace(self):
        assert _parse_size("  8M ") == 8 * 1024**2

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty size"):
            _parse_size("")

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid size"):
            _parse_size("abc")

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="positive"):
            _parse_size("0")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="positive"):
            _parse_size("-1")


# ---------------------------------------------------------------------------
# apply_memory_limit
# ---------------------------------------------------------------------------


class TestApplyMemoryLimit:
    # apply_memory_limit now prepends ["env", "-u", "BASH_ENV"] to every
    # returned command so that bash starts with BASH_ENV unset at the
    # process-environment level.  The returned list is always:
    #   ["env", "-u", "BASH_ENV", <bash>, "-c", <script>, ...]
    # so result[0:3] == ["env", "-u", "BASH_ENV"], result[3] is the bash
    # binary, result[4] == "-c", and result[5] (or result[-1]) is the script.
    ENV_PREFIX = ["env", "-u", "BASH_ENV"]

    def test_none_limit_returns_unchanged(self):
        cmd = ["bash"]
        assert apply_memory_limit(cmd, None) is cmd

    def test_persistent_shell_form(self):
        # 512 MiB → 524288 KiB; hard-gate (&&) so a failed ulimit aborts the shell.
        # Result: ["env", "-u", "BASH_ENV", "bash", "-c", "ulimit -v 524288 && BASH_ENV='' exec bash"]
        result = apply_memory_limit(["bash"], 512 * 1024**2)
        assert result[:3] == self.ENV_PREFIX
        assert result[3] == "bash"
        assert result[4] == "-c"
        script = result[5]
        assert "ulimit -v 524288" in script
        assert "exec bash" in script

    def test_command_form_prepends_ulimit(self):
        # Result: ["env", "-u", "BASH_ENV", "bash", "-c", "ulimit -v 1024 && { echo hi\n}"]
        result = apply_memory_limit(["bash", "-c", "echo hi"], 1024 * 1024)
        assert result[:3] == self.ENV_PREFIX
        assert result[3] == "bash"
        assert result[4] == "-c"
        script = result[5]
        assert "ulimit -v 1024" in script
        assert "echo hi" in script
        # Script must be wrapped in a group so && gates the entire script, not
        # just its first statement (e.g. `ulimit && a; b` runs b even if ulimit fails).
        assert "{ echo hi" in script
        assert script.endswith("}")

    def test_command_form_trailing_comment_not_swallowed(self):
        # A script ending with a # comment must not comment out the closing }.
        # Before fix: `{ echo hi  # note; }` → `; }` commented out, bash error.
        # After fix:  `{ echo hi  # note\n}` → } on its own line, always executes.
        result = apply_memory_limit(
            ["bash", "-c", "echo hi  # trailing comment"], 1024 * 1024
        )
        script = result[-1]
        assert "# trailing comment" in script
        # The closing brace must be on its own line, not on the comment line.
        lines = script.splitlines()
        assert lines[-1].strip() == "}"

    def test_empty_cmd_returns_unchanged(self):
        assert apply_memory_limit([], 1024) == []

    def test_small_limit_rounds_up_to_one_kib(self):
        result = apply_memory_limit(["bash"], 1)
        script = result[-1]
        assert "ulimit -v 1" in script
        assert "exec bash" in script

    def test_non_kib_aligned_limit_rounds_up(self):
        # 2000 bytes is not KiB-aligned; floor gives 1 KiB (under-allocates),
        # ceiling gives 2 KiB (at least the requested amount).
        result = apply_memory_limit(["bash"], 2000)
        script = result[-1]
        assert "ulimit -v 2" in script
        assert "exec bash" in script

    def test_uses_hard_gate_not_soft_fallback(self):
        # The prefix must use && so a failed ulimit aborts the shell rather than
        # running it unrestricted.  verify_memory_limit() surfaces failures at
        # the Python level before the shell is spawned.
        result = apply_memory_limit(["bash"], 512 * 1024**2)
        script = result[-1]
        assert " && " in script and "exec" in script

    def test_both_forms_use_env_u_bash_env(self):
        # Both forms must prepend env -u BASH_ENV so that bash starts with
        # BASH_ENV unset at the OS-environment level.  Bash sources BASH_ENV
        # before executing any -c script; without this, startup code in
        # $BASH_ENV runs before ulimit is installed.
        persistent = apply_memory_limit(["bash"], 512 * 1024**2)
        one_shot = apply_memory_limit(["bash", "-c", "echo hi"], 512 * 1024**2)
        assert persistent[:3] == self.ENV_PREFIX, (
            "persistent form missing env -u BASH_ENV prefix"
        )
        assert one_shot[:3] == self.ENV_PREFIX, (
            "one-shot form missing env -u BASH_ENV prefix"
        )

    def test_hard_limit_set_before_soft_limit(self):
        # Both forms must set the hard limit (ulimit -H -v) before the soft
        # limit so subprocesses cannot raise the soft limit back to unlimited
        # via `ulimit -v unlimited`.  Hard-limit lowering is best-effort
        # (suppressed with 2>/dev/null) so pre-existing lower hard limits are
        # respected; the soft-limit command still fails fast if kib > hard.
        persistent = apply_memory_limit(["bash"], 512 * 1024**2)
        one_shot = apply_memory_limit(["bash", "-c", "echo hi"], 512 * 1024**2)
        for name, result in (("persistent", persistent), ("one-shot", one_shot)):
            script = result[-1]
            hard_pos = script.index("ulimit -H -v ")
            soft_pos = script.index("ulimit -v ", hard_pos + 1)
            assert hard_pos < soft_pos, (
                f"{name} form: hard-limit command must precede soft-limit command"
            )
            # Hard limit is best-effort (2>/dev/null) so a pre-existing lower
            # hard limit doesn't crash the shell; the soft ulimit gates the &&.
            assert "2>/dev/null" in script[hard_pos:soft_pos], (
                f"{name} form: ulimit -H -v must be followed by 2>/dev/null"
            )

    def test_persistent_form_clears_bash_env(self):
        # The -c script must also clear BASH_ENV inline before exec so the
        # replacement shell inherits a clean environment (belt-and-suspenders:
        # env handles the outer shell, the inline assignment handles the exec'd
        # shell which may have started with its own environment).
        result = apply_memory_limit(["bash"], 512 * 1024**2)
        script = result[-1]
        # BASH_ENV='' must appear between ulimit and exec
        ulimit_pos = script.index("ulimit")
        bash_env_pos = script.index("BASH_ENV=''")
        exec_pos = script.index("exec")
        assert ulimit_pos < bash_env_pos < exec_pos

    def test_persistent_form_preserves_extra_flags(self):
        # apply_memory_limit(["bash", "--norc"], N) must not drop --norc so the
        # function honours its contract for callers that pass startup flags.
        result = apply_memory_limit(["bash", "--norc"], 512 * 1024**2)
        assert result[:3] == self.ENV_PREFIX
        assert result[3] == "bash"
        assert result[4] == "-c"
        assert "exec bash --norc" in result[-1]


# ---------------------------------------------------------------------------
# verify_memory_limit
# ---------------------------------------------------------------------------


class TestGetMemoryLimitWindowsGuard:
    """_get_memory_limit() must return None on Windows without calling the verifier."""

    def test_returns_none_on_windows_without_calling_verifier(self, monkeypatch):
        """When _is_windows is True the function exits early; verify_memory_limit
        is never called, so FileNotFoundError from missing bash never surfaces."""
        monkeypatch.setenv("GPTME_SHELL_MEMORY_LIMIT", "256M")
        with (
            patch("gptme.tools.shell._is_windows", True),
            patch("gptme.tools.shell.verify_memory_limit") as mock_verify,
        ):
            from gptme.tools.shell import _get_memory_limit

            result = _get_memory_limit()
        assert result is None
        mock_verify.assert_not_called()


class TestVerifyMemoryLimit:
    def test_raises_when_ulimit_fails(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.returncode = 1
        with (
            patch("gptme.sandbox.subprocess.run", return_value=result),
            pytest.raises(ValueError, match="cannot be enforced"),
        ):
            verify_memory_limit(512 * 1024**2)

    def test_succeeds_when_ulimit_works(self):
        from unittest.mock import MagicMock

        result = MagicMock()
        result.returncode = 0
        with patch("gptme.sandbox.subprocess.run", return_value=result):
            verify_memory_limit(512 * 1024**2)  # should not raise
