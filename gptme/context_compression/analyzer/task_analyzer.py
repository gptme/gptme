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

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
        ratio_ranges: dict[str, tuple[float, float]] | None = None,
    ):
        """Initialize task analyzer with optional custom configuration.

        Args:
            thresholds: Custom complexity thresholds
            ratio_ranges: Custom compression ratio ranges
        """
        self.thresholds = thresholds
        self.ratio_ranges = ratio_ranges

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
        complexity_category = classify_complexity(
            complexity_score, thresholds=self.thresholds
        )

        # Select compression ratio
        compression_ratio = select_compression_ratio(
            complexity_score,
            ratio_ranges=self.ratio_ranges,
            thresholds=self.thresholds,
        )

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

        # Parse enumerated requirements (1. ... 2. ... 3. ...)
        component_count = 0
        enumerated_pattern = r"^\s*(\d+)\.\s+(.+?)(?=^\s*\d+\.\s+|\Z)"
        components = re.findall(
            enumerated_pattern, task_description, re.MULTILINE | re.DOTALL
        )
        if components:
            component_count = len(components)

        # Extract file count (enhanced)
        files_count = 0

        # Method 1: Explicit file count ("5 files", "3 modules")
        file_matches = re.findall(r"(\d+)\s+(?:files?|modules?)", text)
        if file_matches:
            files_count = int(file_matches[0])

        # Method 2: Count .py files mentioned in sub-bullets
        py_file_pattern = r"(\w+\.py)"
        py_files = set(re.findall(py_file_pattern, task_description))
        files_count = max(files_count, len(py_files))

        # Method 3: Count comma-separated module/file names
        # Find lines with comma-separated lists of identifiers
        module_lists_count = 0
        for line in task_description.split("\n"):
            if "," in line and ("_" in line or ".py" in line):
                # Split on commas and count word_word patterns or .py files
                parts = [p.strip() for p in line.split(",")]
                module_count = sum(1 for p in parts if "_" in p or p.endswith(".py"))
                if module_count > 1:
                    module_lists_count += module_count

        # Add module list count to existing file count
        if module_lists_count > 0:
            files_count += module_lists_count

        # Method 4: If no explicit count but have components, use component count as proxy
        if files_count == 0 and component_count > 0:
            files_count = component_count

        # Extract and sum ALL line estimates (not just first)
        lines_estimate = 0
        line_matches = re.findall(r"(?:~)?(\d+)\s+lines?", text)
        if line_matches:
            # Sum all line counts found
            lines_estimate = sum(int(n) for n in line_matches)

        # Detect new files (enhanced with component analysis)
        new_files = any(
            phrase in text
            for phrase in ["create", "new file", "add file", "new module"]
        )
        # If multiple components each describe a module/class, likely new files
        if component_count >= 2:
            component_text = " ".join(comp[1] for comp in components).lower()
            if any(
                word in component_text
                for word in ["module", "class", "file", "package"]
            ):
                new_files = True

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

        # Detect new class creation (enhanced)
        new_classes = 0
        import re

        # Count explicit class names (e.g., "SourceManager class", "PollingLoop class")
        explicit_class_pattern = r"\b([A-Z][a-zA-Z0-9]*)\s+class\b"
        explicit_classes = re.findall(explicit_class_pattern, task_description)
        new_classes = len(explicit_classes)

        # If no explicit names, parse enumerated requirements to count class definitions
        if new_classes == 0:
            enumerated_pattern = r"^\s*(\d+)\.\s+(.+?)(?=^\s*\d+\.\s+|\Z)"
            components = re.findall(
                enumerated_pattern, task_description, re.MULTILINE | re.DOTALL
            )

            if components:
                # Count components that mention classes/definitions
                for _, component_text in components:
                    comp_lower = component_text.lower()
                    if any(
                        keyword in comp_lower
                        for keyword in [
                            "class",
                            "classes",
                            "definitions",
                            "objects",
                            "types",
                        ]
                    ):
                        new_classes += 1

        # Look for patterns like "create 5 new classes" or "add 2 classes"
        numbered_class_matches = re.findall(
            r"(?:create|add)\s+(\d+)\s+(?:new\s+)?class(?:es)?", text
        )
        if numbered_class_matches:
            new_classes = max(new_classes, sum(int(n) for n in numbered_class_matches))

        # Fallback: count individual "create/add class" mentions
        if new_classes == 0:
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

        # Architecture keywords (expanded)
        arch_keywords = {
            "implement",
            "refactor",
            "design",
            "architecture",
            "system",
            "infrastructure",
            "framework",
            "orchestrator",
            "service",
            "coordinator",
            "manager",
            "controller",
        }
        keywords = {kw for kw in arch_keywords if kw in text}

        # Action verbs
        action_verbs = {"fix", "implement", "add", "update", "refactor", "create"}
        verbs = {verb for verb in action_verbs if verb in text}

        # Design mentions (expanded to include more architecture terms)
        mentions_design = any(
            word in text
            for word in [
                "design",
                "architecture",
                "system",
                "orchestrator",
                "service",
                "coordinator",
                "infrastructure",
            ]
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
