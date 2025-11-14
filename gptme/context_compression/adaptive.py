"""Adaptive compression based on task complexity analysis.

The AdaptiveCompressor automatically adjusts compression ratio based on
task characteristics, maintaining quality for complex tasks while enabling
aggressive compression for focused work.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from .analyzer.task_analyzer import AnalysisResult, TaskAnalyzer
from .compressor import CompressionResult, ContextCompressor
from .config import CompressionConfig
from .extractive import ExtractiveSummarizer

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceContext:
    """Workspace context for task analysis.

    Provides information about the workspace environment that helps
    determine task complexity and appropriate compression strategy.
    """

    workspace_path: Path
    active_files: list[Path]
    reference_impls: dict[str, Path]
    tests: dict[str, Path]
    docs: dict[str, Path]

    @classmethod
    def from_cwd(cls, cwd: Path | None = None) -> "WorkspaceContext":
        """Create workspace context from current working directory.

        Args:
            cwd: Current working directory (defaults to Path.cwd())

        Returns:
            WorkspaceContext with discovered workspace information
        """
        if cwd is None:
            cwd = Path.cwd()

        return cls(
            workspace_path=cwd,
            active_files=cls._find_active_files(cwd),
            reference_impls=cls._find_reference_impls(cwd),
            tests=cls._find_tests(cwd),
            docs=cls._find_docs(cwd),
        )

    @staticmethod
    def _find_active_files(path: Path) -> list[Path]:
        """Find recently modified Python files in workspace."""
        if not path.exists():
            return []

        try:
            # Find Python files modified in last 7 days
            py_files = list(path.glob("**/*.py"))
            return sorted(py_files, key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        except (OSError, PermissionError):
            return []

    @staticmethod
    def _find_reference_impls(path: Path) -> dict[str, Path]:
        """Find reference implementations in workspace."""
        refs: dict[str, Path] = {}
        if not path.exists():
            return refs

        try:
            # Look for common patterns: examples/, lib/, src/
            for pattern in ["examples/**/*.py", "lib/**/*.py", "src/**/*.py"]:
                for file in path.glob(pattern):
                    refs[file.stem] = file
        except (OSError, PermissionError):
            pass

        return refs

    @staticmethod
    def _find_tests(path: Path) -> dict[str, Path]:
        """Find test files in workspace."""
        tests: dict[str, Path] = {}
        if not path.exists():
            return tests

        try:
            # Look for test files
            for pattern in ["tests/**/*.py", "test_*.py", "**/test_*.py"]:
                for file in path.glob(pattern):
                    tests[file.stem] = file
        except (OSError, PermissionError):
            pass

        return tests

    @staticmethod
    def _find_docs(path: Path) -> dict[str, Path]:
        """Find documentation files in workspace."""
        docs: dict[str, Path] = {}
        if not path.exists():
            return docs

        try:
            # Look for documentation
            for pattern in ["docs/**/*.md", "*.md", "**/*.md"]:
                for file in path.glob(pattern):
                    docs[file.stem] = file
        except (OSError, PermissionError):
            pass

        return docs


class AdaptiveCompressor(ContextCompressor):
    """Context compressor that adapts to task complexity.

    Analyzes task characteristics to determine appropriate compression ratio:
    - Focused tasks (fixes, debugging): Aggressive compression (0.10-0.20)
    - Mixed tasks (refactoring): Moderate compression (0.20-0.30)
    - Architecture tasks (implementations): Conservative compression (0.30-0.50)
    """

    def __init__(
        self,
        config: CompressionConfig,
        analyzer: TaskAnalyzer | None = None,
        base_compressor: ExtractiveSummarizer | None = None,
        log_analysis: bool | None = None,
    ):
        """Initialize adaptive compressor.

        Args:
            config: Compression configuration
            analyzer: Task analyzer (creates default if None)
            base_compressor: Base compressor (creates default if None)
            log_analysis: Whether to log analysis results (defaults to config.log_analysis)
        """
        self.config = config
        self.analyzer = analyzer or TaskAnalyzer()
        self.base = base_compressor or ExtractiveSummarizer(config)
        self.log_analysis = (
            log_analysis if log_analysis is not None else config.log_analysis
        )

    def compress(
        self,
        content: str,
        target_ratio: float = 0.7,
        context: str = "",
    ) -> CompressionResult:
        """Compress content with task-aware ratio selection.

        Args:
            content: Text to compress
            target_ratio: Ignored (ratio determined by task analysis)
            context: Conversation context containing task description

        Returns:
            CompressionResult with compressed text and metrics
        """
        # Extract task description from context (first non-empty line)
        task_description = self._extract_task_description(context)

        # Create workspace context
        workspace_ctx = WorkspaceContext.from_cwd()

        # Analyze task complexity (pass workspace path, not full context)
        analysis = self.analyzer.analyze(task_description, workspace_ctx.workspace_path)

        # Log analysis if enabled
        if self.log_analysis:
            self._log_analysis(analysis, task_description)

        # Perform compression with task-analyzed ratio
        result = self.base.compress(
            content=content,
            target_ratio=analysis.compression_ratio,
            context=context,
        )

        return result

    def _extract_task_description(self, context: str) -> str:
        """Extract task description from conversation context.

        Args:
            context: Full conversation context

        Returns:
            Task description (first non-empty line or full context)
        """
        if not context:
            return ""

        # Try to find first non-empty line as task description
        lines = context.strip().split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped

        # Fallback to full context (truncated)
        return context[:500]

    def _log_analysis(self, analysis: AnalysisResult, task_description: str) -> None:
        """Log task analysis results for debugging.

        Args:
            analysis: Analysis result
            task_description: Task description that was analyzed
        """
        logger.info(
            "Task analysis: complexity=%.2f (%s), ratio=%.2f (%s), "
            "reduction=~%.0f%%\n"
            "Indicators: files=%d, lines=%d, deps=%d, patterns=%d, refs=%d\n"
            "Task: %s",
            analysis.complexity_score,
            analysis.complexity_category,
            analysis.compression_ratio,
            analysis.ratio_category,
            analysis.estimated_reduction * 100,
            analysis.indicators.scope.files_count,
            analysis.indicators.scope.lines_estimate,
            len(analysis.indicators.dependencies.external_libs),
            len(analysis.indicators.patterns.keywords),
            len(analysis.indicators.context.reference_impls),
            task_description[:100] + ("..." if len(task_description) > 100 else ""),
        )
