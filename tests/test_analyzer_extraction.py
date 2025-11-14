"""Tests for indicator extraction methods in TaskAnalyzer.

Tests the Week 2 implementation of indicator extraction from task descriptions
and workspace analysis.
"""

from pathlib import Path

import pytest

from gptme.context_compression.analyzer import TaskAnalyzer


@pytest.fixture
def analyzer():
    """Create TaskAnalyzer instance."""
    return TaskAnalyzer()


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with test files."""
    # Create test structure
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("# test file")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "demo.py").write_text("# example")
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Documentation")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("# source code")
    return tmp_path


class TestScopeExtraction:
    """Tests for _extract_scope method."""

    def test_extracts_file_count(self, analyzer):
        """Should extract file count from description."""
        description = "Modify 5 files to implement feature"
        result = analyzer._extract_scope(description)
        assert result.files_count == 5

    def test_extracts_lines_estimate(self, analyzer):
        """Should extract lines estimate from description."""
        description = "Add 200 lines of code to implement handler"
        result = analyzer._extract_scope(description)
        assert result.lines_estimate == 200

    def test_detects_new_files(self, analyzer):
        """Should detect when creating new files."""
        description = "Create new module for data processing"
        result = analyzer._extract_scope(description)
        assert result.new_files is True

    def test_detects_edit_mode(self, analyzer):
        """Should detect when editing existing files."""
        description = "Update existing config to add option"
        result = analyzer._extract_scope(description)
        assert result.new_files is False

    def test_extracts_file_types(self, analyzer):
        """Should extract file types mentioned."""
        description = "Create .py and .md files for feature"
        result = analyzer._extract_scope(description)
        assert "py" in result.file_types
        assert "md" in result.file_types

    def test_empty_description_returns_defaults(self, analyzer):
        """Should return default values for empty description."""
        result = analyzer._extract_scope(None)
        assert result.files_count == 0
        assert result.lines_estimate == 0
        assert result.new_files is False
        assert len(result.file_types) == 0


class TestDependencyExtraction:
    """Tests for _extract_dependencies method."""

    def test_detects_external_libraries(self, analyzer):
        """Should detect mentions of known external libraries."""
        description = "Use pytest and requests to implement tests"
        result = analyzer._extract_dependencies(description)
        assert "pytest" in result.external_libs
        assert "requests" in result.external_libs

    def test_detects_internal_modules(self, analyzer):
        """Should detect references to internal modules."""
        description = "Extend tool system and message handling"
        result = analyzer._extract_dependencies(description)
        assert "tool" in result.internal_modules
        assert "message" in result.internal_modules

    def test_counts_new_classes(self, analyzer):
        """Should count new class creations."""
        description = "Create class DataProcessor and add class Handler"
        result = analyzer._extract_dependencies(description)
        assert result.new_classes == 2

    def test_empty_description_returns_defaults(self, analyzer):
        """Should return default values for empty description."""
        result = analyzer._extract_dependencies(None)
        assert len(result.external_libs) == 0
        assert len(result.internal_modules) == 0
        assert result.new_classes == 0


class TestPatternExtraction:
    """Tests for _extract_patterns method."""

    def test_extracts_keywords(self, analyzer):
        """Should extract architecture keywords."""
        description = "Implement new system architecture for framework"
        result = analyzer._extract_patterns(description)
        assert "implement" in result.keywords
        assert "system" in result.keywords
        assert "architecture" in result.keywords
        assert "framework" in result.keywords

    def test_extracts_verbs(self, analyzer):
        """Should extract action verbs."""
        description = "Fix bug and implement feature, then update docs"
        result = analyzer._extract_patterns(description)
        assert "fix" in result.verbs
        assert "implement" in result.verbs
        assert "update" in result.verbs

    def test_detects_design_mentions(self, analyzer):
        """Should detect design-related language."""
        description = "Design the architecture for the system"
        result = analyzer._extract_patterns(description)
        assert result.mentions_design is True

    def test_detects_reference_mentions(self, analyzer):
        """Should detect references to examples."""
        description = "Implement feature similar to existing handler"
        result = analyzer._extract_patterns(description)
        assert result.mentions_reference is True

    def test_empty_description_returns_defaults(self, analyzer):
        """Should return default values for empty description."""
        result = analyzer._extract_patterns(None)
        assert len(result.keywords) == 0
        assert len(result.verbs) == 0
        assert result.mentions_design is False
        assert result.mentions_reference is False


class TestContextExtraction:
    """Tests for _extract_context method."""

    def test_detects_tests(self, analyzer, temp_workspace):
        """Should detect presence of test files."""
        result = analyzer._extract_context(temp_workspace)
        assert result.tests_exist is True

    def test_detects_examples(self, analyzer, temp_workspace):
        """Should detect presence of examples."""
        result = analyzer._extract_context(temp_workspace)
        assert result.examples_available is True

    def test_detects_docs(self, analyzer, temp_workspace):
        """Should detect presence of documentation."""
        result = analyzer._extract_context(temp_workspace)
        assert result.docs_exist is True

    def test_finds_reference_implementations(self, analyzer, temp_workspace):
        """Should find Python files as reference implementations."""
        result = analyzer._extract_context(temp_workspace)
        assert len(result.reference_impls) > 0
        # Should find module.py
        assert any("module.py" in ref for ref in result.reference_impls)

    def test_none_workspace_returns_defaults(self, analyzer):
        """Should return defaults for None workspace."""
        result = analyzer._extract_context(None)
        assert result.tests_exist is False
        assert result.examples_available is False
        assert result.docs_exist is False
        assert len(result.reference_impls) == 0

    def test_nonexistent_workspace_returns_defaults(self, analyzer):
        """Should return defaults for non-existent workspace."""
        result = analyzer._extract_context(Path("/nonexistent/path"))
        assert result.tests_exist is False
        assert result.examples_available is False
        assert result.docs_exist is False
        assert len(result.reference_impls) == 0


class TestEndToEndExtraction:
    """End-to-end tests for indicator extraction."""

    def test_focused_task_extraction(self, analyzer):
        """Should extract indicators for focused diagnostic task."""
        description = "Fix bug in 2 files, add 50 lines of pytest tests"
        result = analyzer.analyze(description, None)

        # Should detect focused task pattern
        assert result.indicators.scope.files_count == 2
        assert result.indicators.scope.lines_estimate == 50
        assert "pytest" in result.indicators.dependencies.external_libs
        assert "fix" in result.indicators.patterns.verbs

        # Should select aggressive compression
        assert result.compression_ratio < 0.25
        assert result.complexity_category == "focused"

    def test_architecture_task_extraction(self, analyzer, temp_workspace):
        """Should extract indicators for architecture-heavy task."""
        description = """
        Design and implement new system architecture with 10 files.
        Create 5 new classes for the framework.
        Implement infrastructure similar to existing patterns.
        Estimated 500 lines of code.
        """
        result = analyzer.analyze(description, temp_workspace)

        # Should detect architecture pattern
        assert result.indicators.scope.files_count == 10
        assert result.indicators.scope.lines_estimate == 500
        assert result.indicators.dependencies.new_classes == 5
        assert result.indicators.patterns.mentions_design is True
        assert result.indicators.patterns.mentions_reference is True

        # Should detect available context
        assert result.indicators.context.tests_exist is True
        assert result.indicators.context.examples_available is True

        # Should select conservative compression
        assert result.compression_ratio > 0.30
        assert result.complexity_category == "architecture"

    def test_mixed_task_extraction(self, analyzer):
        """Should extract indicators for mixed complexity task."""
        description = """
        Refactor 5 files to improve tool system architecture.
        Update 200 lines, create 2 new classes.
        Similar to existing message handler pattern.
        """
        result = analyzer.analyze(description, None)

        # Should detect mixed pattern
        assert result.indicators.scope.files_count == 5
        assert result.indicators.scope.lines_estimate == 200
        assert result.indicators.dependencies.new_classes == 2
        assert "refactor" in result.indicators.patterns.keywords
        assert "tool" in result.indicators.dependencies.internal_modules
        assert result.indicators.patterns.mentions_design is True
        assert result.indicators.patterns.mentions_reference is True

        # Should select moderate compression (mixed task)
        assert 0.20 <= result.compression_ratio <= 0.40
        assert result.complexity_category in ["mixed", "architecture"]
