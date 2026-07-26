"""Tests for gptme.sandbox — sandbox wrapper module (Idea #834)."""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.sandbox import (
    _DEFAULT_ENV_ALLOWLIST,
    SandboxConfig,
    _bwrap_cmd,
    _firejail_cmd,
    build_env,
    sandbox_exec_python,
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
