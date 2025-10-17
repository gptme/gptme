"""Metrics tracking for shell command validation effectiveness."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass
class ValidationMetrics:
    """Metrics for validation effectiveness over time."""

    # Counts
    total_validations: int = 0
    total_warnings: int = 0
    total_errors: int = 0
    commands_blocked: int = 0  # In strict mode

    # By rule type
    warnings_by_rule: dict[str, int] = field(default_factory=dict)
    errors_by_rule: dict[str, int] = field(default_factory=dict)

    # By severity
    warning_severity_counts: dict[str, int] = field(
        default_factory=lambda: {"error": 0, "warning": 0}
    )

    # Session info
    session_start: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def record_validation(
        self,
        is_valid: bool,
        warnings: list,
        validation_level: Literal["strict", "warn", "off"],
    ) -> None:
        """Record a validation event.

        Args:
            is_valid: Whether command passed validation
            warnings: List of ValidationWarning objects
            validation_level: Validation mode used
        """
        self.total_validations += 1
        self.total_warnings += len(warnings)

        if not is_valid and validation_level == "strict":
            self.commands_blocked += 1

        for warning in warnings:
            # Count by severity
            self.warning_severity_counts[warning.severity] += 1

            if warning.severity == "error":
                self.total_errors += 1

            # Extract rule name from warning message or lesson link
            rule_name = self._extract_rule_name(warning)

            # Count by rule
            if warning.severity == "error":
                self.errors_by_rule[rule_name] = (
                    self.errors_by_rule.get(rule_name, 0) + 1
                )
            else:
                self.warnings_by_rule[rule_name] = (
                    self.warnings_by_rule.get(rule_name, 0) + 1
                )

        self.last_updated = datetime.now().isoformat()

    def _extract_rule_name(self, warning) -> str:
        """Extract rule name from warning."""
        # Try to extract from lesson link
        if warning.lesson:
            # Extract filename from lesson path
            lesson_path = Path(warning.lesson)
            return lesson_path.stem

        # Fallback: use first few words of message
        words = warning.message.split()[:3]
        return "-".join(w.lower() for w in words)

    def generate_report(self) -> str:
        """Generate a human-readable effectiveness report.

        Returns:
            Formatted report string
        """
        report_lines = [
            "# Shell Validation Metrics Report",
            f"\nSession: {self.session_start} to {self.last_updated}",
            "\n## Summary",
            f"- Total validations: {self.total_validations}",
            f"- Total warnings: {self.total_warnings}",
            f"- Total errors: {self.total_errors}",
            f"- Commands blocked: {self.commands_blocked}",
        ]

        if self.total_validations > 0:
            warning_rate = (self.total_warnings / self.total_validations) * 100
            report_lines.append(f"- Warning rate: {warning_rate:.1f}%")

        report_lines.extend(
            [
                "\n## Warnings by Rule",
            ]
        )

        # Sort rules by frequency
        sorted_warnings = sorted(
            self.warnings_by_rule.items(), key=lambda x: x[1], reverse=True
        )

        if sorted_warnings:
            for rule, count in sorted_warnings:
                report_lines.append(f"- {rule}: {count}")
        else:
            report_lines.append("- No warnings recorded")

        report_lines.extend(
            [
                "\n## Errors by Rule",
            ]
        )

        sorted_errors = sorted(
            self.errors_by_rule.items(), key=lambda x: x[1], reverse=True
        )

        if sorted_errors:
            for rule, count in sorted_errors:
                report_lines.append(f"- {rule}: {count}")
        else:
            report_lines.append("- No errors recorded")

        report_lines.extend(
            [
                "\n## Severity Distribution",
                f"- Warnings: {self.warning_severity_counts['warning']}",
                f"- Errors: {self.warning_severity_counts['error']}",
            ]
        )

        return "\n".join(report_lines)

    def save(self, path: Path | str) -> None:
        """Save metrics to JSON file.

        Args:
            path: Path to save metrics file
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path | str) -> "ValidationMetrics":
        """Load metrics from JSON file.

        Args:
            path: Path to metrics file

        Returns:
            ValidationMetrics instance
        """
        with open(path) as f:
            data = json.load(f)

        return cls(**data)

    def merge(self, other: "ValidationMetrics") -> None:
        """Merge another metrics instance into this one.

        Useful for aggregating metrics across sessions.

        Args:
            other: Another ValidationMetrics instance
        """
        self.total_validations += other.total_validations
        self.total_warnings += other.total_warnings
        self.total_errors += other.total_errors
        self.commands_blocked += other.commands_blocked

        # Merge rule counts
        for rule, count in other.warnings_by_rule.items():
            self.warnings_by_rule[rule] = self.warnings_by_rule.get(rule, 0) + count

        for rule, count in other.errors_by_rule.items():
            self.errors_by_rule[rule] = self.errors_by_rule.get(rule, 0) + count

        # Merge severity counts
        for severity, count in other.warning_severity_counts.items():
            self.warning_severity_counts[severity] = (
                self.warning_severity_counts.get(severity, 0) + count
            )

        self.last_updated = datetime.now().isoformat()
