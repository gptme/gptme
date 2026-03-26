"""Tests for util/install.py - installation environment detection and command generation."""

import sys
from unittest.mock import patch

import pytest

from gptme.util.install import detect_install_environment, get_package_install_command


class TestDetectInstallEnvironment:
    """Tests for detect_install_environment()."""

    def test_pipx_via_env_var(self, monkeypatch):
        """PIPX_HOME env var indicates pipx installation."""
        monkeypatch.setenv("PIPX_HOME", "/home/user/.local/pipx")
        monkeypatch.delenv("UV_HOME", raising=False)
        assert detect_install_environment() == "pipx"

    def test_pipx_via_prefix(self, monkeypatch):
        """sys.prefix containing 'pipx/venvs' indicates pipx installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        with (
            patch.object(sys, "prefix", "/home/user/.local/pipx/venvs/gptme"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "pipx"

    def test_uvx_via_env_var(self, monkeypatch):
        """UV_HOME env var indicates uvx installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.setenv("UV_HOME", "/home/user/.uv")
        with (
            patch.object(sys, "prefix", "/home/user/other"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "uvx"

    def test_uvx_via_prefix_dotslash(self, monkeypatch):
        """sys.prefix containing '/.uv/' indicates uvx installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        with (
            patch.object(sys, "prefix", "/home/user/.uv/python/cpython-3.12/lib"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "uvx"

    def test_uvx_via_prefix_slash_uv(self, monkeypatch):
        """sys.prefix containing '/uv/' indicates uvx installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        with (
            patch.object(sys, "prefix", "/home/user/.local/uv/envs/gptme"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "uvx"

    def test_venv_via_prefix(self, monkeypatch):
        """Different sys.prefix vs base_prefix indicates venv installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        with (
            patch.object(sys, "prefix", "/home/user/myproject/.venv"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "venv"

    def test_system_when_prefix_equals_base_prefix(self, monkeypatch):
        """Same sys.prefix and base_prefix indicates system installation."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        with (
            patch.object(sys, "prefix", "/usr"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            assert detect_install_environment() == "system"

    def test_pipx_takes_precedence_over_uv(self, monkeypatch):
        """PIPX_HOME takes precedence when both are set."""
        monkeypatch.setenv("PIPX_HOME", "/home/user/.local/pipx")
        monkeypatch.setenv("UV_HOME", "/home/user/.uv")
        assert detect_install_environment() == "pipx"

    def test_return_type_is_string(self, monkeypatch):
        """Return value is always a string."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        result = detect_install_environment()
        assert isinstance(result, str)

    def test_return_value_in_valid_set(self, monkeypatch):
        """Return value is always one of the known environment types."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        result = detect_install_environment()
        assert result in {"pipx", "uvx", "venv", "system"}


class TestGetPackageInstallCommand:
    """Tests for get_package_install_command()."""

    def test_pipx_simple_package(self):
        """pipx environment uses 'pipx inject gptme'."""
        cmd = get_package_install_command("questionary", env_type="pipx")
        assert cmd == "pipx inject gptme questionary"

    def test_pipx_package_with_extra(self):
        """pipx handles extras in package name."""
        cmd = get_package_install_command("gptme[browser]", env_type="pipx")
        assert cmd == "pipx inject gptme gptme[browser]"

    def test_uvx_simple_package(self):
        """uvx environment uses 'uv pip install'."""
        cmd = get_package_install_command("questionary", env_type="uvx")
        assert cmd == "uv pip install questionary"

    def test_uvx_package_with_extra(self):
        """uvx handles extras in package name."""
        cmd = get_package_install_command("gptme[browser]", env_type="uvx")
        assert cmd == "uv pip install gptme[browser]"

    def test_venv_simple_package(self):
        """venv environment uses 'pip install'."""
        cmd = get_package_install_command("questionary", env_type="venv")
        assert cmd == "pip install questionary"

    def test_venv_package_with_extra(self):
        """venv handles extras."""
        cmd = get_package_install_command("gptme[browser]", env_type="venv")
        assert cmd == "pip install gptme[browser]"

    def test_system_simple_package(self):
        """system environment uses 'pip install --user'."""
        cmd = get_package_install_command("questionary", env_type="system")
        assert cmd == "pip install --user questionary"

    def test_system_package_with_extra(self):
        """system handles extras."""
        cmd = get_package_install_command("gptme[browser]", env_type="system")
        assert cmd == "pip install --user gptme[browser]"

    def test_auto_detect_env_type_when_none(self, monkeypatch):
        """Auto-detects environment when env_type is None."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.delenv("UV_HOME", raising=False)
        # Just verify it doesn't crash and returns a string
        cmd = get_package_install_command("questionary")
        assert isinstance(cmd, str)
        assert "questionary" in cmd

    def test_returns_string(self):
        """Return value is always a string."""
        for env in ("pipx", "uvx", "venv", "system"):
            result = get_package_install_command("pkg", env_type=env)
            assert isinstance(result, str)

    def test_package_name_in_output(self):
        """Package name always appears in the command."""
        for env in ("pipx", "uvx", "venv", "system"):
            cmd = get_package_install_command("mypackage", env_type=env)
            assert "mypackage" in cmd

    def test_pipx_command_starts_with_pipx(self):
        """pipx command starts with 'pipx'."""
        cmd = get_package_install_command("pkg", env_type="pipx")
        assert cmd.startswith("pipx")

    def test_uvx_command_starts_with_uv(self):
        """uvx command starts with 'uv'."""
        cmd = get_package_install_command("pkg", env_type="uvx")
        assert cmd.startswith("uv")

    def test_venv_and_system_use_pip(self):
        """venv and system commands use pip."""
        for env in ("venv", "system"):
            cmd = get_package_install_command("pkg", env_type=env)
            assert "pip" in cmd

    @pytest.mark.parametrize(
        "package",
        [
            "questionary",
            "playwright",
            "gptme[browser]",
            "gptme[computer]",
            "some-package-with-dashes",
            "package_with_underscores",
        ],
    )
    def test_various_package_names(self, package):
        """Various package name formats are handled correctly."""
        for env in ("pipx", "uvx", "venv", "system"):
            cmd = get_package_install_command(package, env_type=env)
            assert package in cmd

    def test_auto_detect_pipx_via_env_var(self, monkeypatch):
        """Auto-detect returns pipx command when PIPX_HOME is set."""
        monkeypatch.setenv("PIPX_HOME", "/home/user/.local/pipx")
        cmd = get_package_install_command("questionary")
        assert "pipx" in cmd

    def test_auto_detect_uvx_via_env_var(self, monkeypatch):
        """Auto-detect returns uvx command when UV_HOME is set."""
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.setenv("UV_HOME", "/home/user/.uv")
        with (
            patch.object(sys, "prefix", "/home/user/other"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            cmd = get_package_install_command("questionary")
            assert "uv" in cmd
