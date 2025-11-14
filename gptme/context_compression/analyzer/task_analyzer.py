"""Main task analysis interface for adaptive compression.

The TaskAnalyzer provides the primary API for analyzing tasks and
determining appropriate compression ratios.

Usage:
    analyzer = TaskAnalyzer()
    result = analyzer.analyze(task_description, workspace_context)
    ratio = result.compression_ratio
"""

from dataclasses import dataclass
from pathlib import Path

from .indicators import (
    ContextIndicators,
    DependencyIndicators,
    PatternIndicators,
    ScopeIndicators,
    TaskIndicators,
)
from .ratio_selector import (
    estimate_reduction,
    get_ratio_category,
    select_compression_ratio,
)
from .scorer import calculate_complexity_score, classify_complexity


@dataclass
class AnalysisResult:
    """Result of task complexity analysis."""

    indicators: TaskIndicators
    complexity_score: float  # 0.0-1.0
    complexity_category: str  # "focused", "mixed", "architecture"
    compression_ratio: float  # 0.10-0.50
    ratio_category: str  # "aggressive", "moderate", "conservative"
    estimated_reduction: float  # 0.0-1.0 (percentage)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "indicators": self.indicators.to_dict(),
            "complexity_score": self.complexity_score,
            "complexity_category": self.complexity_category,
            "compression_ratio": self.compression_ratio,
            "ratio_category": self.ratio_category,
            "estimated_reduction": self.estimated_reduction,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        return (
            f"Task Complexity: {self.complexity_category} "
            f"(score: {self.complexity_score:.2f})\n"
            f"Compression: {self.ratio_category} "
            f"(ratio: {self.compression_ratio:.2f}, "
            f"~{self.estimated_reduction*100:.0f}% reduction)"
        )


class TaskAnalyzer:
    """Main task analysis interface for adaptive compression.

    Analyzes task characteristics and determines appropriate compression ratio.
    """

    def analyze(
        self,
        task_description: str | None = None,
        workspace_path: Path | None = None,
    ) -> AnalysisResult:
        """Analyze task and determine compression strategy.

        Args:
            task_description: Task description text (from prompt, issue, etc.)
            workspace_path: Path to workspace for context detection

        Returns:
            AnalysisResult with compression recommendation
        """
        # Extract indicators (Week 2 implementation)
        indicators = self._extract_indicators(task_description, workspace_path)

        # Calculate complexity score
        complexity_score = calculate_complexity_score(indicators)

        # Classify complexity
        complexity_category = classify_complexity(complexity_score)

        # Select compression ratio
        compression_ratio = select_compression_ratio(complexity_score)

        # Get ratio category
        ratio_category = get_ratio_category(compression_ratio)

        # Estimate reduction
        estimated_reduction = estimate_reduction(compression_ratio)

        return AnalysisResult(
            indicators=indicators,
            complexity_score=complexity_score,
            complexity_category=complexity_category,
            compression_ratio=compression_ratio,
            ratio_category=ratio_category,
            estimated_reduction=estimated_reduction,
        )

    def _extract_indicators(
        self,
        task_description: str | None,
        workspace_path: Path | None,
    ) -> TaskIndicators:
        """Extract task indicators from description and workspace.

        Parses task description for complexity signals and analyzes workspace
        for available context resources.
        """
        scope = self._extract_scope(task_description)
        dependencies = self._extract_dependencies(task_description)
        patterns = self._extract_patterns(task_description)
        context = self._extract_context(workspace_path)

        return TaskIndicators(
            scope=scope,
            dependencies=dependencies,
            patterns=patterns,
            context=context,
        )

    def _extract_scope(self, task_description: str | None) -> ScopeIndicators:
        """Extract scope indicators from task description."""
        if not task_description:
            return ScopeIndicators()

        import re

        text = task_description.lower()

        # Extract file count
        files_count = 0
        file_matches = re.findall(r"(\d+)\s+files?", text)
        if file_matches:
            files_count = int(file_matches[0])

        # Extract lines estimate
        lines_estimate = 0
        line_matches = re.findall(r"(\d+)\s+lines?", text)
        if line_matches:
            lines_estimate = int(line_matches[0])

        # Detect new files
        new_files = any(
            phrase in text
            for phrase in ["create", "new file", "add file", "new module"]
        )

        # Extract file types
        file_types = set()
        type_patterns = [
            (r"\.py\b", "py"),
            (r"\.md\b", "md"),
            (r"\.yaml\b", "yaml"),
            (r"\.yml\b", "yml"),
            (r"\.json\b", "json"),
            (r"\.toml\b", "toml"),
        ]
        for pattern, ext in type_patterns:
            if re.search(pattern, text):
                file_types.add(ext)

        return ScopeIndicators(
            files_count=files_count,
            lines_estimate=lines_estimate,
            new_files=new_files,
            file_types=file_types,
        )

    def _extract_dependencies(
        self, task_description: str | None
    ) -> DependencyIndicators:
        """Extract dependency indicators from task description."""
        if not task_description:
            return DependencyIndicators()

        text = task_description.lower()

        # Common external libraries
        known_libs = {
            "pytest",
            "numpy",
            "pandas",
            "requests",
            "flask",
            "django",
            "fastapi",
            "pydantic",
        }
        external_libs = {lib for lib in known_libs if lib in text}

        # Internal modules (gptme-specific)
        internal_keywords = {"tool", "message", "config", "util", "context"}
        internal_modules = {kw for kw in internal_keywords if kw in text}

        # Detect new class creation
        new_classes = 0
        import re

        # Look for patterns like "create 5 new classes" or "add 2 classes"
        numbered_class_matches = re.findall(
            r"(?:create|add)\s+(\d+)\s+(?:new\s+)?class(?:es)?", text
        )
        if numbered_class_matches:
            new_classes = sum(int(n) for n in numbered_class_matches)
        else:
            # Fallback: count individual "create/add class" mentions
            class_matches = re.findall(r"(?:create|add|new)\s+class", text)
            new_classes = len(class_matches)

        return DependencyIndicators(
            external_libs=external_libs,
            internal_modules=internal_modules,
            new_classes=new_classes,
            inheritance_depth=0,  # Hard to detect from description
        )

    def _extract_patterns(self, task_description: str | None) -> PatternIndicators:
        """Extract pattern indicators from task description."""
        if not task_description:
            return PatternIndicators()

        text = task_description.lower()

        # Architecture keywords
        arch_keywords = {
            "implement",
            "refactor",
            "design",
            "architecture",
            "system",
            "infrastructure",
            "framework",
        }
        keywords = {kw for kw in arch_keywords if kw in text}

        # Action verbs
        action_verbs = {"fix", "implement", "add", "update", "refactor", "create"}
        verbs = {verb for verb in action_verbs if verb in text}

        # Design mentions
        mentions_design = any(
            word in text for word in ["design", "architecture", "system"]
        )

        # Reference mentions
        mentions_reference = any(
            phrase in text for phrase in ["like", "similar to", "based on", "example"]
        )

        return PatternIndicators(
            keywords=keywords,
            verbs=verbs,
            mentions_design=mentions_design,
            mentions_reference=mentions_reference,
        )

    def _extract_context(self, workspace_path: Path | None) -> ContextIndicators:
        """Extract context indicators from workspace."""
        if not workspace_path or not workspace_path.exists():
            return ContextIndicators()

        reference_impls = []
        examples_available = False
        tests_exist = False
        docs_exist = False

        # Check for test files
        test_patterns = ["test_*.py", "*_test.py", "tests/"]
        for pattern in test_patterns:
            if list(workspace_path.glob(pattern)):
                tests_exist = True
                break

        # Check for examples
        example_patterns = ["examples/", "example.py", "demo.py"]
        for pattern in example_patterns:
            if list(workspace_path.glob(pattern)):
                examples_available = True
                break

        # Check for documentation
        doc_patterns = ["docs/", "*.md", "README*"]
        for pattern in doc_patterns:
            if list(workspace_path.glob(pattern)):
                docs_exist = True
                break

        # Find reference implementations
        py_files = list(workspace_path.glob("**/*.py"))
        if py_files:
            reference_impls = [str(f.relative_to(workspace_path)) for f in py_files[:5]]

        return ContextIndicators(
            reference_impls=reference_impls,
            examples_available=examples_available,
            tests_exist=tests_exist,
            docs_exist=docs_exist,
        )
