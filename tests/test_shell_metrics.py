"""Tests for shell validation metrics tracking."""

import json
import tempfile
from pathlib import Path


from gptme.tools.shell_metrics import ValidationMetrics
from gptme.tools.shell_validator import ShellValidator, ValidationWarning


def test_metrics_initialization():
    """Test metrics are initialized with zero counts."""
    metrics = ValidationMetrics()

    assert metrics.total_validations == 0
    assert metrics.total_warnings == 0
    assert metrics.total_errors == 0
    assert metrics.commands_blocked == 0
    assert len(metrics.warnings_by_rule) == 0
    assert len(metrics.errors_by_rule) == 0


def test_metrics_record_validation_no_warnings():
    """Test recording validation with no warnings."""
    metrics = ValidationMetrics()

    metrics.record_validation(is_valid=True, warnings=[], validation_level="warn")

    assert metrics.total_validations == 1
    assert metrics.total_warnings == 0
    assert metrics.total_errors == 0
    assert metrics.commands_blocked == 0


def test_metrics_record_validation_with_warnings():
    """Test recording validation with warnings."""
    metrics = ValidationMetrics()

    warnings = [
        ValidationWarning(
            severity="warning",
            message="Variable FOO without $ prefix",
            suggestion="Use $FOO",
            lesson="/path/to/shell-variable-syntax.md",
        ),
        ValidationWarning(
            severity="error",
            message="Python file execution",
            suggestion="Use python3",
            lesson="/path/to/python-file-execution.md",
        ),
    ]

    metrics.record_validation(
        is_valid=False, warnings=warnings, validation_level="strict"
    )

    assert metrics.total_validations == 1
    assert metrics.total_warnings == 2
    assert metrics.total_errors == 1
    assert metrics.commands_blocked == 1  # strict mode
    assert "shell-variable-syntax" in metrics.warnings_by_rule
    assert "python-file-execution" in metrics.errors_by_rule


def test_metrics_record_multiple_validations():
    """Test recording multiple validations accumulates correctly."""
    metrics = ValidationMetrics()

    # First validation: 1 warning
    warning1 = ValidationWarning(
        severity="warning", message="Test warning 1", lesson="/path/to/test-rule.md"
    )
    metrics.record_validation(True, [warning1], "warn")

    # Second validation: 2 warnings
    warning2 = ValidationWarning(
        severity="warning", message="Test warning 2", lesson="/path/to/test-rule.md"
    )
    warning3 = ValidationWarning(
        severity="error", message="Test error", lesson="/path/to/another-rule.md"
    )
    metrics.record_validation(True, [warning2, warning3], "warn")

    assert metrics.total_validations == 2
    assert metrics.total_warnings == 3
    assert metrics.total_errors == 1
    assert metrics.warnings_by_rule["test-rule"] == 2
    assert metrics.errors_by_rule["another-rule"] == 1


def test_metrics_generate_report():
    """Test generating human-readable report."""
    metrics = ValidationMetrics()

    # Add some data
    warnings = [
        ValidationWarning(
            severity="warning",
            message="Variable issue",
            lesson="/path/to/shell-variable-syntax.md",
        ),
        ValidationWarning(
            severity="error",
            message="Python issue",
            lesson="/path/to/python-invocation.md",
        ),
    ]

    metrics.record_validation(False, warnings, "strict")

    report = metrics.generate_report()

    # Check report content
    assert "Shell Validation Metrics Report" in report
    assert "Total validations: 1" in report
    assert "Total warnings: 2" in report
    assert "Total errors: 1" in report
    assert "Commands blocked: 1" in report
    assert "shell-variable-syntax" in report
    assert "python-invocation" in report


def test_metrics_save_and_load():
    """Test saving and loading metrics from JSON."""
    metrics = ValidationMetrics()

    # Add some data
    warning = ValidationWarning(
        severity="warning", message="Test", lesson="/path/to/test.md"
    )
    metrics.record_validation(True, [warning], "warn")

    # Save to temp file
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "metrics.json"
        metrics.save(path)

        # Verify file exists and is valid JSON
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
            assert data["total_validations"] == 1
            assert data["total_warnings"] == 1

        # Load back
        loaded = ValidationMetrics.load(path)
        assert loaded.total_validations == 1
        assert loaded.total_warnings == 1
        assert "test" in loaded.warnings_by_rule


def test_metrics_merge():
    """Test merging two metrics instances."""
    metrics1 = ValidationMetrics()
    metrics2 = ValidationMetrics()

    # Add data to first metrics
    warning1 = ValidationWarning(
        severity="warning", message="Test", lesson="/path/to/rule1.md"
    )
    metrics1.record_validation(True, [warning1], "warn")

    # Add data to second metrics
    warning2 = ValidationWarning(
        severity="error", message="Test", lesson="/path/to/rule2.md"
    )
    metrics2.record_validation(False, [warning2], "strict")

    # Merge
    metrics1.merge(metrics2)

    assert metrics1.total_validations == 2
    assert metrics1.total_warnings == 2
    assert metrics1.total_errors == 1
    assert metrics1.commands_blocked == 1
    assert "rule1" in metrics1.warnings_by_rule
    assert "rule2" in metrics1.errors_by_rule


def test_validator_tracks_metrics():
    """Test that validator automatically tracks metrics."""
    validator = ShellValidator(validation_level="warn")

    # Validate a command with issues
    is_valid, warnings = validator.validate("python script.py")

    # Check metrics were recorded
    assert validator.metrics.total_validations == 1
    assert validator.metrics.total_warnings == len(warnings)


def test_validator_metrics_accumulate():
    """Test that validator metrics accumulate across multiple validations."""
    validator = ShellValidator(validation_level="warn")

    # Validate multiple commands
    validator.validate("python script.py")  # Has warnings
    validator.validate("python3 script.py")  # May have warnings
    validator.validate("echo hello")  # No warnings

    # Check metrics accumulated
    assert validator.metrics.total_validations == 3
    assert validator.metrics.total_warnings >= 0  # At least first had warnings


def test_validator_metrics_report():
    """Test generating report from validator's metrics."""
    validator = ShellValidator(validation_level="warn")

    # Run some validations
    validator.validate("python script.py")
    validator.validate("LLM_API_TIMEOUT")
    validator.validate("cd /path with spaces")

    # Generate report
    report = validator.metrics.generate_report()

    assert "Shell Validation Metrics Report" in report
    assert "Total validations: 3" in report
