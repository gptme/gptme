"""Enhanced success criteria validators for validation suite.

Provides custom validators for different types of success criteria,
beyond the basic keyword/file/commit checks.
"""

import re
import subprocess
from pathlib import Path


class CriterionValidator:
    """Base class for custom validators."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        """Check if criterion is met."""
        raise NotImplementedError


class FileExistsValidator(CriterionValidator):
    """Validate file existence with pattern matching."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Extract file patterns from criterion
        # Patterns like "*.py", "README.md", "journal/*.md"
        patterns = re.findall(r"[\w\-./]+\.\w+|\*\.\w+", criterion)

        for pattern in patterns:
            if "*" in pattern:
                # Glob pattern
                if list(workspace.rglob(pattern)):
                    return True
            else:
                # Exact file
                if (workspace / pattern).exists():
                    return True

        return False


class GitCommitValidator(CriterionValidator):
    """Validate git commit with message matching."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                capture_output=True,
                text=True,
                cwd=workspace,
                check=True,
            )

            # Extract keywords (words >3 chars, alphanumeric)
            keywords = [word.lower() for word in re.findall(r"\b\w{4,}\b", criterion)]

            log_lower = result.stdout.lower()

            # Require at least 2 keywords to match
            matches = sum(1 for kw in keywords if kw in log_lower)
            return matches >= min(2, len(keywords))

        except Exception:
            return False


class TestPassValidator(CriterionValidator):
    """Validate test execution success."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Check for common test success indicators
        success_patterns = [
            r"(\d+) passed",
            r"all tests? passed",
            r"OK \(\d+ tests?\)",
            r"test.*success",
        ]

        output_lower = output.lower()

        # Check for success patterns
        for pattern in success_patterns:
            if re.search(pattern, output_lower):
                return True

        # Check for absence of failure patterns
        failure_patterns = [
            r"(\d+) failed",
            r"FAILED",
            r"ERROR",
        ]

        has_failures = any(
            re.search(pattern, output_lower) for pattern in failure_patterns
        )

        return not has_failures


class CIPassValidator(CriterionValidator):
    """Validate CI check success."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Check for CI-specific patterns
        patterns = [
            r"checks?.*passed",
            r"build.*success",
            r"ci.*green",
            r"all checks? passing",
        ]

        output_lower = output.lower()
        return any(re.search(pattern, output_lower) for pattern in patterns)


class PRCreatedValidator(CriterionValidator):
    """Validate PR creation."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Check for PR creation indicators
        patterns = [
            r"https://github\.com/[\w\-]+/[\w\-]+/pull/\d+",
            r"PR #\d+ created",
            r"pull request.*created",
        ]

        return any(re.search(pattern, output) for pattern in patterns)


class CodeQualityValidator(CriterionValidator):
    """Validate code quality (no TODO/FIXME, proper formatting)."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Check for code quality issues in workspace
        python_files = list(workspace.rglob("*.py"))

        if not python_files:
            return True  # No Python files to check

        # Check for TODO/FIXME comments
        for py_file in python_files:
            content = py_file.read_text()
            if re.search(r"# (TODO|FIXME)", content, re.IGNORECASE):
                return False

        return True


class ContentMatchValidator(CriterionValidator):
    """Validate specific content exists in output or files."""

    def validate(self, criterion: str, workspace: Path, output: str) -> bool:
        # Extract quoted strings as required content
        required_content = re.findall(r'"([^"]+)"', criterion)

        if not required_content:
            # Fallback to keyword matching
            keywords = re.findall(r"\b\w{4,}\b", criterion.lower())
            output_lower = output.lower()
            return any(kw in output_lower for kw in keywords)

        # Check if all required content appears
        output_lower = output.lower()
        return all(content.lower() in output_lower for content in required_content)


class ValidatorFactory:
    """Factory for creating appropriate validators."""

    VALIDATORS = {
        "file": FileExistsValidator,
        "exists": FileExistsValidator,
        "commit": GitCommitValidator,
        "message": GitCommitValidator,
        "test": TestPassValidator,
        "passed": TestPassValidator,
        "ci": CIPassValidator,
        "checks": CIPassValidator,
        "pr": PRCreatedValidator,
        "pull request": PRCreatedValidator,
        "quality": CodeQualityValidator,
        "no TODO": CodeQualityValidator,
        "content": ContentMatchValidator,
        "contains": ContentMatchValidator,
    }

    @classmethod
    def get_validator(cls, criterion: str) -> CriterionValidator:
        """Get appropriate validator for criterion."""
        criterion_lower = criterion.lower()

        # Match validator by keywords
        for keyword, validator_cls in cls.VALIDATORS.items():
            if keyword in criterion_lower:
                return validator_cls()

        # Default to content matching
        return ContentMatchValidator()


def validate_criterion(criterion: str, workspace: Path, output: str) -> bool:
    """Validate a single success criterion using appropriate validator."""
    validator = ValidatorFactory.get_validator(criterion)
    return validator.validate(criterion, workspace, output)


def validate_all_criteria(
    criteria: list[str], workspace: Path, output: str
) -> dict[str, bool]:
    """Validate all success criteria for a task."""
    return {
        criterion: validate_criterion(criterion, workspace, output)
        for criterion in criteria
    }
