"""Tests for shell validation rule configuration."""

from gptme.config import ShellValidationConfig
from gptme.tools.shell_validator import ShellValidator


class TestRuleConfiguration:
    """Test per-rule enable/disable configuration."""

    def test_all_rules_enabled_by_default(self):
        """All rules should run when no config provided."""
        validator = ShellValidator()

        # Command with multiple issues
        cmd = "python script.py && cd /Programming/gptme && echo LLM_API_TIMEOUT"
        is_valid, warnings = validator.validate(cmd)

        # Should detect multiple issues (python, directory path, bare variable)
        assert len(warnings) >= 3

    def test_disable_single_rule(self):
        """Should be able to disable specific rule."""
        config = ShellValidationConfig(
            enabled=True, level="warn", rules={"bare_variables": False}
        )
        validator = ShellValidator(config=config)

        # Command with bare variable
        cmd = "echo LLM_API_TIMEOUT"
        is_valid, warnings = validator.validate(cmd)

        # Should not detect bare variable (rule disabled)
        assert len(warnings) == 0

    def test_disable_multiple_rules(self):
        """Should be able to disable multiple rules."""
        config = ShellValidationConfig(
            enabled=True,
            level="warn",
            rules={
                "bare_variables": False,
                "python_invocation": False,
            },
        )
        validator = ShellValidator(config=config)

        # Command with bare variable and python invocation
        cmd = "python script.py && echo LLM_API_TIMEOUT"
        is_valid, warnings = validator.validate(cmd)

        # Should not detect either issue
        assert len(warnings) == 0

    def test_enable_specific_rule_only(self):
        """Should be able to enable only specific rules."""
        config = ShellValidationConfig(
            enabled=True,
            level="warn",
            rules={
                "bare_variables": True,
                "python_invocation": False,
                "python_file_execution": False,
                "path_quoting": False,
                "directory_paths": False,
            },
        )
        validator = ShellValidator(config=config)

        # Command with multiple issues
        cmd = "python ./script.py && cd /path with spaces && echo LLM_API_TIMEOUT"
        is_valid, warnings = validator.validate(cmd)

        # Should only detect bare variable
        assert len(warnings) == 1
        assert "LLM_API_TIMEOUT" in warnings[0].message

    def test_rule_config_empty_dict_enables_all(self):
        """Empty rules dict should enable all rules."""
        config = ShellValidationConfig(enabled=True, level="warn", rules={})
        validator = ShellValidator(config=config)

        # Command with issue
        cmd = "python script.py"
        is_valid, warnings = validator.validate(cmd)

        # Should detect issue
        assert len(warnings) > 0

    def test_rule_config_precedence(self):
        """Config should override default behavior."""
        # Default: all rules enabled
        validator_default = ShellValidator(validation_level="warn")

        # Config: python_invocation disabled
        config = ShellValidationConfig(
            enabled=True, level="warn", rules={"python_invocation": False}
        )
        validator_config = ShellValidator(config=config)

        cmd = "python script.py"

        # Default validator should detect
        _, warnings_default = validator_default.validate(cmd)
        assert len(warnings_default) > 0

        # Config validator should not detect
        _, warnings_config = validator_config.validate(cmd)
        assert len(warnings_config) == 0

    def test_selective_strictness(self):
        """Can be strict on some rules, lenient on others."""
        # Disable noisy rules, keep critical ones
        config = ShellValidationConfig(
            enabled=True,
            level="warn",
            rules={
                "bare_variables": True,  # Keep (critical)
                "python_invocation": True,  # Keep (critical)
                "path_quoting": False,  # Disable (can be noisy)
                "directory_paths": False,  # Disable (environment-specific)
            },
        )
        validator = ShellValidator(config=config)

        # Command with multiple issues
        cmd = "python script.py && cd /path with spaces && echo LLM_API_TIMEOUT"
        is_valid, warnings = validator.validate(cmd)

        # Should detect python and bare variable, but not path quoting
        warning_msgs = [w.message for w in warnings]
        assert any("python" in msg.lower() for msg in warning_msgs)
        assert any("LLM_API_TIMEOUT" in msg for msg in warning_msgs)
        # Path quoting should not be detected (disabled)
        assert len(warnings) == 2
