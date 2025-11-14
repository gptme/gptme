"""Indicator extraction for task complexity analysis.

Defines data structures for the four indicator categories:
1. ScopeIndicators: Files/lines impacted
2. DependencyIndicators: Imports/references
3. PatternIndicators: Task description keywords
4. ContextIndicators: Available resources
"""

from dataclasses import dataclass, field


@dataclass
class ScopeIndicators:
    """Indicators about task scope (files/lines impacted)."""

    files_count: int = 0  # Number of files to modify
    lines_estimate: int = 0  # Estimated lines of code
    new_files: bool = False  # Creating new files vs editing
    file_types: set[str] = field(default_factory=set)  # py, md, yaml, etc.

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "files_count": self.files_count,
            "lines_estimate": self.lines_estimate,
            "new_files": self.new_files,
            "file_types": list(self.file_types),
        }


@dataclass
class DependencyIndicators:
    """Indicators about dependencies and imports."""

    external_libs: set[str] = field(default_factory=set)  # Third-party dependencies
    internal_modules: set[str] = field(
        default_factory=set
    )  # Workspace module references
    new_classes: int = 0  # New class definitions
    inheritance_depth: int = 0  # Class hierarchy complexity

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "external_libs": list(self.external_libs),
            "internal_modules": list(self.internal_modules),
            "new_classes": self.new_classes,
            "inheritance_depth": self.inheritance_depth,
        }


@dataclass
class PatternIndicators:
    """Indicators from task description patterns."""

    keywords: set[str] = field(default_factory=set)  # Task type keywords
    verbs: set[str] = field(default_factory=set)  # Action verbs (fix, implement, etc.)
    mentions_design: bool = False  # Contains "design", "architecture", "system"
    mentions_reference: bool = False  # Contains "like", "similar to", "based on"

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "keywords": list(self.keywords),
            "verbs": list(self.verbs),
            "mentions_design": self.mentions_design,
            "mentions_reference": self.mentions_reference,
        }


@dataclass
class ContextIndicators:
    """Indicators about available context resources."""

    reference_impls: list[str] = field(default_factory=list)  # Existing implementations
    examples_available: bool = False  # Example code exists
    tests_exist: bool = False  # Test files present
    docs_exist: bool = False  # Documentation available

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "reference_impls": self.reference_impls,
            "examples_available": self.examples_available,
            "tests_exist": self.tests_exist,
            "docs_exist": self.docs_exist,
        }


@dataclass
class TaskIndicators:
    """Complete set of task complexity indicators."""

    scope: ScopeIndicators = field(default_factory=ScopeIndicators)
    dependencies: DependencyIndicators = field(default_factory=DependencyIndicators)
    patterns: PatternIndicators = field(default_factory=PatternIndicators)
    context: ContextIndicators = field(default_factory=ContextIndicators)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "scope": self.scope.to_dict(),
            "dependencies": self.dependencies.to_dict(),
            "patterns": self.patterns.to_dict(),
            "context": self.context.to_dict(),
        }
