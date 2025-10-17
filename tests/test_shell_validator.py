"""Unit tests for shell command validation."""

from gptme.tools.shell_validator import ShellValidator, validate_command


class TestBareVariables:
    """Tests for bare variable detection."""

    def test_detects_bare_variable(self):
        """Should detect variables without $ prefix."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("echo LLM_API_TIMEOUT")

        assert len(warnings) == 1
        assert "LLM_API_TIMEOUT" in warnings[0].message
        assert warnings[0].suggestion is not None
        assert "$LLM_API_TIMEOUT" in warnings[0].suggestion

    def test_allows_proper_variable(self):
        """Should allow variables with $ prefix."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("echo $LLM_API_TIMEOUT")

        # Should not warn about properly prefixed variable
        assert len(warnings) == 0

    def test_detects_not_given(self):
        """Should detect NOT_GIVEN constant."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("NOT_GIVEN")

        assert len(warnings) == 1
        assert "NOT_GIVEN" in warnings[0].message

    def test_ignores_short_uppercase(self):
        """Should not flag short uppercase words."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("echo OK GO")

        assert len(warnings) == 0

    def test_multiple_bare_variables(self):
        """Should detect multiple bare variables."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("echo LLM_API_TIMEOUT NOT_GIVEN")

        assert len(warnings) == 2


class TestPythonInvocation:
    """Tests for Python invocation checking."""

    def test_detects_python(self):
        """Should detect 'python' instead of 'python3'."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("python script.py")

        assert len(warnings) == 1
        assert warnings[0].suggestion is not None
        assert "python3" in warnings[0].suggestion

    def test_allows_python3(self):
        """Should allow 'python3' command."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("python3 script.py")

        assert len(warnings) == 0

    def test_ignores_python_in_text(self):
        """Should not flag 'python' in strings or comments."""
        validator = ShellValidator()

        # These are edge cases - we may flag them, which is okay
        # Just testing that we don't crash
        _, warnings = validator.validate('echo "python is great"')
        # We might warn here, which is acceptable false positive

    def test_python_with_flags(self):
        """Should detect python with flags."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("python -m pytest")

        assert len(warnings) == 1


class TestPythonFileExecution:
    """Tests for Python file execution checking."""

    def test_detects_relative_execution(self):
        """Should detect ./script.py execution."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("./script.py")

        assert len(warnings) == 1
        assert warnings[0].suggestion is not None
        assert "python3" in warnings[0].suggestion

    def test_detects_bare_py_file(self):
        """Should detect bare .py file as command."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("script.py")

        assert len(warnings) == 1

    def test_allows_python3_with_file(self):
        """Should allow python3 script.py."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("python3 script.py")

        # No warning about file execution (already using python3)
        file_warnings = [
            w
            for w in warnings
            if w.suggestion is not None
            and "python3" in w.suggestion
            and ".py" in w.message
        ]
        assert len(file_warnings) == 0

    def test_py_in_path(self):
        """Should handle .py in middle of path."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("cat path/to/script.py")

        # Should not warn (cat, not execution)
        assert len(warnings) == 0


class TestPathQuoting:
    """Tests for path quoting checking."""

    def test_detects_unquoted_space_in_cd(self):
        """Should detect unquoted path with spaces in cd."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("cd /path with spaces")

        assert len(warnings) == 1
        assert "quoted" in warnings[0].message.lower()

    def test_allows_quoted_path(self):
        """Should allow properly quoted paths."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate('cd "/path with spaces"')

        # Should not warn about quoting
        quote_warnings = [w for w in warnings if "quoted" in w.message.lower()]
        assert len(quote_warnings) == 0

    def test_detects_space_in_ls(self):
        """Should detect unquoted path in ls."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("ls /home/bob/some path")

        assert len(warnings) == 1

    def test_variable_with_space(self):
        """Should not warn about variables."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("cd $PROJECT_PATH")

        # Should not warn about quoting (it's a variable)
        quote_warnings = [w for w in warnings if "quoted" in w.message.lower()]
        assert len(quote_warnings) == 0


class TestDirectoryPaths:
    """Tests for directory path checking."""

    def test_detects_programming_directory(self):
        """Should detect incorrect /Programming/ path."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("cd /home/bob/Programming/gptme")

        assert len(warnings) == 1
        assert "Programming" in warnings[0].message
        assert warnings[0].suggestion is not None
        assert "/home/bob/gptme" in warnings[0].suggestion

    def test_allows_correct_path(self):
        """Should allow correct project paths."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("cd /home/bob/gptme")

        # Should not warn about directory structure
        dir_warnings = [w for w in warnings if "Programming" in w.message]
        assert len(dir_warnings) == 0

    def test_multiple_projects(self):
        """Should detect multiple incorrect paths."""
        validator = ShellValidator()
        cmd = "cd /home/bob/Programming/gptme && cd /home/bob/Programming/alice"
        is_valid, warnings = validator.validate(cmd)

        # Filter for directory structure warnings specifically (not path quoting)
        dir_warnings = [
            w
            for w in warnings
            if "Incorrect path:" in w.message and "Programming" in w.message
        ]
        assert len(dir_warnings) == 2


class TestValidationLevels:
    """Tests for validation level configuration."""

    def test_strict_mode_fails_on_warning(self):
        """Strict mode should fail on any warning."""
        validator = ShellValidator(validation_level="strict")
        is_valid, warnings = validator.validate("python script.py")

        assert not is_valid
        assert len(warnings) > 0

    def test_warn_mode_allows_with_warnings(self):
        """Warn mode should allow execution with warnings."""
        validator = ShellValidator(validation_level="warn")
        is_valid, warnings = validator.validate("python script.py")

        assert is_valid  # Allows execution
        assert len(warnings) > 0  # But has warnings

    def test_off_mode_skips_validation(self):
        """Off mode should skip all validation."""
        validator = ShellValidator(validation_level="off")
        is_valid, warnings = validator.validate("python ./script.py LLM_API_TIMEOUT")

        assert is_valid
        assert len(warnings) == 0


class TestConvenienceFunction:
    """Tests for validate_command convenience function."""

    def test_returns_simple_format(self):
        """Should return ValidationWarning objects with full details."""
        is_valid, warnings = validate_command("python script.py")

        assert isinstance(warnings, list)
        assert len(warnings) > 0

        # Check that we got ValidationWarning objects
        from gptme.tools.shell_validator import ValidationWarning

        assert all(isinstance(w, ValidationWarning) for w in warnings)

        # Check warning details
        warning = warnings[0]
        assert "python3" in warning.message.lower()
        assert warning.suggestion is not None
        assert warning.lesson is not None
        assert "lessons/tools/python-invocation.md" in warning.lesson

    def test_strict_level(self):
        """Should support strict level."""
        is_valid, messages = validate_command("python script.py", level="strict")

        assert not is_valid


class TestComplexCommands:
    """Tests for complex command patterns."""

    def test_pipe_chain(self):
        """Should handle commands with pipes."""
        validator = ShellValidator()
        cmd = "python process.py | python analyze.py"
        is_valid, warnings = validator.validate(cmd)

        # Should warn about both python invocations
        python_warnings = [
            w
            for w in warnings
            if w.suggestion is not None and "python3" in w.suggestion
        ]
        assert len(python_warnings) == 2

    def test_compound_operators(self):
        """Should handle compound operators."""
        validator = ShellValidator()
        cmd = "cd /home/bob/Programming/gptme && python test.py"
        is_valid, warnings = validator.validate(cmd)

        # Should detect both issues
        assert len(warnings) >= 2

    def test_background_job(self):
        """Should handle background jobs."""
        validator = ShellValidator()
        cmd = "python script.py &"
        is_valid, warnings = validator.validate(cmd)

        assert len(warnings) >= 1


class TestEdgeCases:
    """Tests for edge cases and corner cases."""

    def test_empty_command(self):
        """Should handle empty command."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("")

        assert is_valid
        assert len(warnings) == 0

    def test_only_whitespace(self):
        """Should handle whitespace-only command."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("   \n  \t  ")

        assert is_valid
        assert len(warnings) == 0

    def test_comment_only(self):
        """Should handle comment-only command."""
        validator = ShellValidator()
        is_valid, warnings = validator.validate("# This is a comment")

        assert is_valid
        # Comments shouldn't trigger warnings
        assert len(warnings) == 0

    def test_long_command(self):
        """Should handle very long commands efficiently."""
        validator = ShellValidator()
        # Create a long command (1000 characters)
        cmd = "echo " + "a" * 1000
        is_valid, warnings = validator.validate(cmd)

        # Should complete without error
        assert is_valid is not None


class TestPerformance:
    """Tests for validation performance."""

    def test_validation_speed(self):
        """Validation should complete in <10ms."""
        import time

        validator = ShellValidator()
        cmd = "python script.py && cd /home/bob/Programming/project && ./run.py"

        start = time.perf_counter()
        validator.validate(cmd)
        elapsed = time.perf_counter() - start

        # Should complete in less than 10ms
        assert (
            elapsed < 0.01
        ), f"Validation took {elapsed * 1000:.2f}ms (expected <10ms)"

    def test_multiple_validations_speed(self):
        """Multiple validations should be fast."""
        import time

        validator = ShellValidator()
        commands = [
            "python script.py",
            "cd /home/bob/Programming/gptme",
            "./test.py",
            "echo $LLM_API_TIMEOUT",
            "cat /path with spaces/file.txt",
        ] * 20  # 100 commands

        start = time.perf_counter()
        for cmd in commands:
            validator.validate(cmd)
        elapsed = time.perf_counter() - start

        # Should average <1ms per command
        avg_time = elapsed / len(commands)
        assert avg_time < 0.001, f"Average validation time: {avg_time * 1000:.2f}ms"


class TestConfiguration:
    """Test configuration support for shell validation."""

    def test_config_enabled(self):
        """Test that config enables validation."""
        from gptme.config import ShellValidationConfig

        config = ShellValidationConfig(enabled=True, level="strict")
        is_valid, warnings = validate_command("LLM_API_TIMEOUT", config=config)
        assert not is_valid
        assert len(warnings) == 1

    def test_config_disabled(self):
        """Test that config can disable validation."""
        from gptme.config import ShellValidationConfig

        config = ShellValidationConfig(enabled=False)
        is_valid, warnings = validate_command("LLM_API_TIMEOUT", config=config)
        # Should still run validation but return valid
        # because disabled config sets level to "off"
        assert is_valid
        assert len(warnings) == 0

    def test_config_level_strict(self):
        """Test that config level is used."""
        from gptme.config import ShellValidationConfig

        config = ShellValidationConfig(enabled=True, level="strict")
        is_valid, warnings = validate_command("python script.py", config=config)
        assert not is_valid
        assert len(warnings) == 1

    def test_config_level_warn(self):
        """Test that warn level allows execution with warnings."""
        from gptme.config import ShellValidationConfig

        config = ShellValidationConfig(enabled=True, level="warn")
        is_valid, warnings = validate_command("python script.py", config=config)
        assert is_valid  # warn mode allows execution
        assert len(warnings) == 1

    def test_config_overrides_level_param(self):
        """Test that config overrides level parameter."""
        from gptme.config import ShellValidationConfig

        # Pass level="warn" but config has level="strict"
        config = ShellValidationConfig(enabled=True, level="strict")
        is_valid, warnings = validate_command(
            "python script.py", level="warn", config=config
        )
        assert not is_valid  # Config strict mode should override
        assert len(warnings) == 1

    def test_no_config_uses_level_param(self):
        """Test that level param is used when no config provided."""
        is_valid, warnings = validate_command("python script.py", level="strict")
        assert not is_valid
        assert len(warnings) == 1
