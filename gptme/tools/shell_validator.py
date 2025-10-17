"""Shell command validation to prevent common errors."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .shell_metrics import ValidationMetrics

if TYPE_CHECKING:
    from ..config import ShellValidationConfig


@dataclass
class ValidationWarning:
    """A validation warning with severity, suggested fix, and documentation link."""

    severity: Literal["error", "warning"]
    message: str
    suggestion: str | None = None
    lesson: str | None = None  # Link to relevant lesson documentation


class ShellValidator:
    """Validate shell commands before execution to prevent common errors."""

    # Known environment variables that might appear as bare identifiers
    KNOWN_VARS = {
        "LLM_API_TIMEOUT",
        "NOT_GIVEN",
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "PWD",
        "OLDPWD",
        "LANG",
        "LC_ALL",
        "TERM",
        "DISPLAY",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }

    def __init__(
        self,
        validation_level: Literal["strict", "warn", "off"] = "warn",
        custom_rules: list | None = None,
        config: "ShellValidationConfig | None" = None,
    ):
        """Initialize validator with configuration.

        Args:
            validation_level: How to handle validation failures (overridden by config if provided)
                - "strict": Fail on any warning
                - "warn": Log warnings but allow execution
            config: Optional ShellValidationConfig from project config
                - "off": Skip validation
            custom_rules: Additional validation rules (not implemented yet)
        """
        # Use config if provided, otherwise use parameters
        if config:
            self.level = config.level if config.enabled else "off"
            self.rules_config = config.rules
        else:
            self.level = validation_level
            self.rules_config = {}

        self.custom_rules = custom_rules or []
        self.metrics = ValidationMetrics()

    def validate(self, cmd: str) -> tuple[bool, list[ValidationWarning]]:
        """Run all validation checks on a shell command.

        Args:
            cmd: Shell command to validate

        Returns:
            (is_valid, warnings): Whether command is valid and list of warnings
        """
        if self.level == "off":
            return True, []

        warnings: list[ValidationWarning] = []

        # Run all validation rules
        warnings.extend(self._check_bare_variables(cmd))
        warnings.extend(self._check_python_invocation(cmd))
        warnings.extend(self._check_python_file_execution(cmd))
        warnings.extend(self._check_path_quoting(cmd))
        warnings.extend(self._check_directory_paths(cmd))

        # In strict mode, any warning makes command invalid
        # In warn mode, only errors make command invalid
        if self.level == "strict":
            is_valid = len(warnings) == 0
        else:
            errors = [w for w in warnings if w.severity == "error"]
            is_valid = len(errors) == 0

        # Record metrics
        self.metrics.record_validation(is_valid, warnings, self.level)  # type: ignore[arg-type]

        return is_valid, warnings

    def _check_bare_variables(self, cmd: str) -> list[ValidationWarning]:
        """Check for variables used without $ prefix.

        Common error: LLM_API_TIMEOUT instead of $LLM_API_TIMEOUT
        """
        warnings = []

        # Pattern: uppercase identifier with underscores (looks like a variable)
        # Must not be preceded by $ or be in a string/comment
        pattern = r"\b([A-Z][A-Z_]{2,})\b"

        for match in re.finditer(pattern, cmd):
            var_name = match.group(1)
            pos = match.start()

            # Skip if preceded by $ (it's already properly referenced)
            if pos > 0 and cmd[pos - 1] == "$":
                continue

            # Check if it's in our list of known variables
            if var_name in self.KNOWN_VARS:
                warnings.append(
                    ValidationWarning(
                        severity="warning",
                        message=f"Possible bare variable '{var_name}' (missing $ prefix)",
                        suggestion=f"Use '${var_name}' if this is a variable reference",
                        lesson="lessons/tools/shell-variable-syntax.md",
                    )
                )

        return warnings

    def _check_python_invocation(self, cmd: str) -> list[ValidationWarning]:
        """Check for use of 'python' instead of 'python3'.

        Common error: python script.py (should be python3 script.py)
        """
        warnings = []

        # Pattern: 'python' not followed by '3' or '-'
        # Use word boundaries to avoid matching 'python3' or 'python-config'
        pattern = r"\bpython\b(?!3|-)"

        for _match in re.finditer(pattern, cmd):
            warnings.append(
                ValidationWarning(
                    severity="warning",
                    message="Using 'python' instead of 'python3'",
                    suggestion="Use 'python3' explicitly to avoid ambiguity",
                    lesson="lessons/tools/python-invocation.md",
                )
            )

        return warnings

    def _check_python_file_execution(self, cmd: str) -> list[ValidationWarning]:
        """Check for direct execution of Python files.

        Common error: ./script.py (should be python3 script.py)
        """
        warnings = []

        # Pattern: .py file being executed directly (at command position)
        # Match ./script.py or script.py at start of line or after command separator
        patterns = [
            r"(?:^|[;&|])\s*(\./[^\s;|&]+\.py)\b",  # ./script.py
            r"(?:^|[;&|])\s*([^\s/]+\.py)\b",  # script.py (at command start)
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, cmd):
                py_file = match.group(1)

                # Skip if preceded by 'python' or 'python3' or common commands
                pos = match.start()
                if pos > 10:
                    prefix = cmd[max(0, pos - 15) : pos].lower()
                    if any(
                        word in prefix
                        for word in [
                            "python",
                            "cat",
                            "vim",
                            "nano",
                            "less",
                            "grep",
                            "chmod",
                        ]
                    ):
                        continue

                warnings.append(
                    ValidationWarning(
                        severity="warning",
                        message=f"Direct execution of Python file: {py_file}",
                        suggestion=f"Use 'python3 {py_file}' instead",
                        lesson="lessons/tools/python-file-execution.md",
                    )
                )

        return warnings

    def _check_path_quoting(self, cmd: str) -> list[ValidationWarning]:
        """Check for unquoted paths that might contain spaces.

        Common error: cd /path with spaces (should be cd "/path with spaces")
        """
        warnings = []

        # Pattern: path-like string with spaces not inside quotes
        # This is heuristic - only flag if it looks like a path
        pattern = r'\b(?:cd|ls|cat|rm|mv|cp)\s+([^"\'\s][^;|&\n]*\s[^;|&\n]*)'

        for match in re.finditer(pattern, cmd):
            path = match.group(1).strip()
            # Skip if already quoted or is a variable
            if path.startswith(("'", '"', "$")):
                continue

            warnings.append(
                ValidationWarning(
                    severity="warning",
                    message=f"Path with spaces should be quoted: {path}",
                    suggestion=f'Use "{path}" with quotes',
                    lesson="lessons/tools/shell-path-quoting.md",
                )
            )

        return warnings

    def _check_directory_paths(self, cmd: str) -> list[ValidationWarning]:
        """Check for incorrect directory assumptions.

        Common error: /home/bob/Programming/PROJECT (should be /home/bob/PROJECT)
        """
        warnings = []

        # Pattern: /home/bob/Programming/ path (common mistake)
        pattern = r"/home/bob/Programming/([a-zA-Z0-9_-]+)"

        for match in re.finditer(pattern, cmd):
            project = match.group(1)
            warnings.append(
                ValidationWarning(
                    severity="warning",
                    message=f"Incorrect path: /home/bob/Programming/{project}",
                    suggestion=f"Projects are at /home/bob/{project}, not in Programming/",
                    lesson="lessons/workflow/directory-structure-awareness.md",
                )
            )

        return warnings


# Convenience function for simple validation
def validate_command(
    cmd: str,
    level: Literal["strict", "warn", "off"] = "warn",
    config: "ShellValidationConfig | None" = None,
) -> tuple[bool, list[ValidationWarning]]:
    """Validate a shell command and return detailed warnings.

    Args:
        cmd: Shell command to validate
        level: Validation level ("strict", "warn", or "off")

    Returns:
        (is_valid, warnings): Validation result with detailed warning objects
    """
    validator = ShellValidator(validation_level=level, config=config)
    is_valid, warnings = validator.validate(cmd)
    return is_valid, warnings
