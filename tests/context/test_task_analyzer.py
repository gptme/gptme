"""Tests for task complexity analyzer.

Validates feature extraction, classification logic, and compression ratio selection
for the adaptive context compression system.
"""

import pytest

from gptme.context.task_analyzer import (
    TaskClassification,
    TaskFeatures,
    _extract_file_impact,
    _extract_prompt_signals,
    _extract_workspace_context,
    classify_task,
    extract_features,
    generate_rationale,
    select_compression_ratio,
)


class TestTaskFeatures:
    """Test TaskFeatures dataclass."""

    def test_default_initialization(self):
        """Test TaskFeatures initializes with defaults."""
        features = TaskFeatures()

        assert features.files_to_modify == 0
        assert features.new_files_count == 0
        assert features.total_file_size == 0
        assert features.file_types == set()
        assert features.directory_spread == 0
        assert features.import_depth == 0
        assert features.external_deps == 0
        assert features.internal_coupling == 0.0
        assert features.circular_deps is False
        assert features.has_reference_impl is False
        assert features.reference_files == []
        assert features.pattern_matches == 0
        assert features.total_workspace_size == 0

        # Verify prompt_signals defaults
        assert "diagnostic_score" in features.prompt_signals
        assert "implementation_score" in features.prompt_signals
        assert "fix_score" in features.prompt_signals
        assert "exploration_score" in features.prompt_signals
        assert "refactor_score" in features.prompt_signals

    def test_custom_initialization(self):
        """Test TaskFeatures with custom values."""
        features = TaskFeatures(
            files_to_modify=3,
            new_files_count=2,
            file_types={".py", ".md"},
            directory_spread=2,
        )

        assert features.files_to_modify == 3
        assert features.new_files_count == 2
        assert features.file_types == {".py", ".md"}
        assert features.directory_spread == 2


class TestTaskClassification:
    """Test TaskClassification dataclass."""

    def test_valid_initialization(self):
        """Test TaskClassification with valid values."""
        classification = TaskClassification(
            primary_type="implementation",
            confidence=0.85,
            secondary_types=["refactor"],
            all_scores={"implementation": 0.85, "refactor": 0.60},
        )

        assert classification.primary_type == "implementation"
        assert classification.confidence == 0.85
        assert classification.secondary_types == ["refactor"]
        assert classification.all_scores == {"implementation": 0.85, "refactor": 0.60}

    def test_invalid_primary_type(self):
        """Test TaskClassification rejects invalid primary_type."""
        with pytest.raises(ValueError, match="Invalid primary_type"):
            TaskClassification(
                primary_type="invalid_type",
                confidence=0.8,
            )

    def test_invalid_confidence_too_low(self):
        """Test TaskClassification rejects confidence < 0.0."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            TaskClassification(
                primary_type="fix",
                confidence=-0.1,
            )

    def test_invalid_confidence_too_high(self):
        """Test TaskClassification rejects confidence > 1.0."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            TaskClassification(
                primary_type="fix",
                confidence=1.5,
            )

    def test_invalid_secondary_type(self):
        """Test TaskClassification rejects invalid secondary_type."""
        with pytest.raises(ValueError, match="Invalid secondary_type"):
            TaskClassification(
                primary_type="fix",
                confidence=0.8,
                secondary_types=["invalid_type"],
            )


class TestPromptSignalExtraction:
    """Test prompt signal extraction."""

    def test_diagnostic_keywords(self):
        """Test detection of diagnostic keywords."""
        prompt = "Debug the failing test and investigate why the error occurs"
        signals = _extract_prompt_signals(prompt)

        assert signals["diagnostic_score"] > 0.5
        assert signals["diagnostic_score"] <= 1.0

    def test_implementation_keywords(self):
        """Test detection of implementation keywords."""
        prompt = "Implement a new feature to create and build the payment system"
        signals = _extract_prompt_signals(prompt)

        assert signals["implementation_score"] > 0.5
        assert signals["implementation_score"] <= 1.0

    def test_fix_keywords(self):
        """Test detection of fix keywords."""
        prompt = "Fix the bug and resolve the issue with the API"
        signals = _extract_prompt_signals(prompt)

        assert signals["fix_score"] > 0.5
        assert signals["fix_score"] <= 1.0

    def test_exploration_keywords(self):
        """Test detection of exploration keywords."""
        prompt = "Research the options and explore alternatives for the architecture"
        signals = _extract_prompt_signals(prompt)

        assert signals["exploration_score"] > 0.5
        assert signals["exploration_score"] <= 1.0

    def test_refactor_keywords(self):
        """Test detection of refactor keywords."""
        prompt = "Refactor the code to improve and simplify the structure"
        signals = _extract_prompt_signals(prompt)

        assert signals["refactor_score"] > 0.5
        assert signals["refactor_score"] <= 1.0

    def test_mixed_keywords(self):
        """Test prompt with multiple keyword types."""
        prompt = "Debug the error and implement a fix for the failing test"
        signals = _extract_prompt_signals(prompt)

        # Should detect both diagnostic and fix signals
        assert signals["diagnostic_score"] > 0.0
        assert signals["fix_score"] > 0.0

    def test_no_keywords(self):
        """Test prompt with no matching keywords."""
        prompt = "Do something with the thing"
        signals = _extract_prompt_signals(prompt)

        # All scores should be 0.0
        assert all(score == 0.0 for score in signals.values())

    def test_case_insensitive(self):
        """Test keyword matching is case-insensitive."""
        prompt = "DEBUG the FAILING test"
        signals = _extract_prompt_signals(prompt)

        assert signals["diagnostic_score"] > 0.0


class TestFileImpactExtraction:
    """Test file impact metrics extraction."""

    def test_single_file(self, tmp_path):
        """Test extraction with single file."""
        file1 = tmp_path / "test.py"
        file1.write_text("print('hello')")

        features = TaskFeatures()
        _extract_file_impact(features, [file1])

        assert features.files_to_modify == 1
        assert features.file_types == {".py"}
        assert features.directory_spread == 1
        assert features.total_file_size > 0

    def test_multiple_files_same_directory(self, tmp_path):
        """Test extraction with multiple files in same directory."""
        file1 = tmp_path / "test1.py"
        file2 = tmp_path / "test2.py"
        file1.write_text("code1")
        file2.write_text("code2")

        features = TaskFeatures()
        _extract_file_impact(features, [file1, file2])

        assert features.files_to_modify == 2
        assert features.file_types == {".py"}
        assert features.directory_spread == 1

    def test_multiple_files_different_directories(self, tmp_path):
        """Test extraction with files in different directories."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        file1 = dir1 / "test1.py"
        file2 = dir2 / "test2.md"
        file1.write_text("code")
        file2.write_text("# doc")

        features = TaskFeatures()
        _extract_file_impact(features, [file1, file2])

        assert features.files_to_modify == 2
        assert features.file_types == {".py", ".md"}
        assert features.directory_spread == 2

    def test_empty_file_list(self):
        """Test extraction with empty file list."""
        features = TaskFeatures()
        _extract_file_impact(features, [])

        assert features.files_to_modify == 0
        assert features.file_types == set()
        assert features.directory_spread == 0


class TestWorkspaceContextExtraction:
    """Test workspace context metrics extraction."""

    def test_basic_context(self):
        """Test extraction with basic context items."""
        context = [
            "Some text content",
            "More content here",
        ]

        features = TaskFeatures()
        _extract_workspace_context(features, context)

        assert features.total_workspace_size > 0
        assert features.total_workspace_size == sum(len(item) for item in context)

    def test_reference_implementation_detection(self):
        """Test detection of reference implementations."""
        context = [
            "class MyClass:\n    def method(self):\n        pass",
            "def function_one():\n    return True",
            "def function_two():\n    return False",
            "class AnotherClass:\n    pass",
        ]

        features = TaskFeatures()
        _extract_workspace_context(features, context)

        assert features.has_reference_impl is True
        assert features.pattern_matches >= 3

    def test_no_reference_implementations(self):
        """Test with context lacking reference implementations."""
        context = [
            "Just some text",
            "More text here",
        ]

        features = TaskFeatures()
        _extract_workspace_context(features, context)

        assert features.has_reference_impl is False
        assert features.pattern_matches < 3

    def test_empty_context(self):
        """Test extraction with empty context."""
        features = TaskFeatures()
        _extract_workspace_context(features, [])

        assert features.total_workspace_size == 0
        assert features.has_reference_impl is False


class TestFeatureExtraction:
    """Test complete feature extraction."""

    def test_extract_features_prompt_only(self):
        """Test feature extraction with only prompt."""
        prompt = "Fix the bug in the authentication system"
        features = extract_features(prompt)

        assert features.prompt_signals["fix_score"] > 0.0
        assert features.files_to_modify == 0
        assert features.total_workspace_size == 0

    def test_extract_features_with_workspace(self, tmp_path):
        """Test feature extraction with workspace files."""
        file1 = tmp_path / "auth.py"
        file1.write_text("authentication code")

        prompt = "Fix the authentication bug"
        features = extract_features(
            prompt=prompt,
            workspace_files=[file1],
        )

        assert features.prompt_signals["fix_score"] > 0.0
        assert features.files_to_modify == 1
        assert features.file_types == {".py"}

    def test_extract_features_complete(self, tmp_path):
        """Test feature extraction with all inputs."""
        file1 = tmp_path / "auth.py"
        file1.write_text("code")

        context = [
            "class AuthHandler:\n    def login(self):\n        pass",
            "def authenticate():\n    return True",
        ]

        prompt = "Fix the authentication bug"
        features = extract_features(
            prompt=prompt,
            workspace_files=[file1],
            current_context=context,
        )

        assert features.prompt_signals["fix_score"] > 0.0
        assert features.files_to_modify == 1
        assert features.total_workspace_size > 0


class TestClassifyTask:
    """Test classify_task() function logic."""

    def test_classify_diagnostic_task(self):
        """Test classification of diagnostic task."""
        features = TaskFeatures(
            files_to_modify=1,
            new_files_count=0,
            prompt_signals={
                "diagnostic_score": 0.9,
                "fix_score": 0.0,
                "implementation_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )

        classification = classify_task(features)

        assert classification.primary_type == "diagnostic"
        assert classification.confidence > 0.0

    def test_classify_fix_task(self):
        """Test classification of fix task."""
        features = TaskFeatures(
            files_to_modify=2,
            new_files_count=0,
            prompt_signals={
                "diagnostic_score": 0.0,
                "fix_score": 0.9,
                "implementation_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )

        classification = classify_task(features)

        assert classification.primary_type == "fix"
        assert classification.confidence > 0.0

    def test_classify_implementation_task(self):
        """Test classification of implementation task."""
        features = TaskFeatures(
            files_to_modify=0,
            new_files_count=5,
            prompt_signals={
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "implementation_score": 0.9,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )

        classification = classify_task(features)

        assert classification.primary_type == "implementation"
        assert classification.confidence > 0.0

    def test_classify_refactor_task(self):
        """Test classification of refactor task."""
        features = TaskFeatures(
            files_to_modify=5,
            new_files_count=0,
            prompt_signals={
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "implementation_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.9,
            },
        )

        classification = classify_task(features)

        assert classification.primary_type == "refactor"
        assert classification.confidence > 0.0

    def test_classify_with_reference_impl(self):
        """Test classification with reference implementation."""
        features = TaskFeatures(
            files_to_modify=0,
            new_files_count=3,
            has_reference_impl=True,
            prompt_signals={
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "implementation_score": 0.6,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )

        classification = classify_task(features)

        assert classification.primary_type == "implementation"
        assert classification.confidence > 0.0


class TestCompressionRatioSelection:
    """Test compression ratio selection logic."""

    def test_diagnostic_ratio(self):
        """Test ratio selection for diagnostic task."""
        classification = TaskClassification(
            primary_type="diagnostic",
            confidence=0.9,
        )
        features = TaskFeatures()

        ratio = select_compression_ratio(classification, features)

        assert 0.10 <= ratio <= 0.15

    def test_fix_ratio(self):
        """Test ratio selection for fix task."""
        classification = TaskClassification(
            primary_type="fix",
            confidence=0.9,
        )
        features = TaskFeatures()

        ratio = select_compression_ratio(classification, features)

        assert 0.15 <= ratio <= 0.20

    def test_implementation_ratio(self):
        """Test ratio selection for implementation task."""
        classification = TaskClassification(
            primary_type="implementation",
            confidence=0.9,
        )
        features = TaskFeatures()

        ratio = select_compression_ratio(classification, features)

        assert 0.30 <= ratio <= 0.50

    def test_implementation_with_reference(self):
        """Test ratio selection for implementation with reference."""
        classification = TaskClassification(
            primary_type="implementation",
            confidence=0.9,
        )
        features = TaskFeatures(
            has_reference_impl=True,
        )

        ratio = select_compression_ratio(classification, features)

        # Should use max ratio to preserve references
        assert ratio >= 0.35

    def test_low_confidence_adjustment(self):
        """Test ratio adjustment for low confidence."""
        classification = TaskClassification(
            primary_type="diagnostic",
            confidence=0.5,
        )
        features = TaskFeatures()

        ratio = select_compression_ratio(classification, features)

        # Low confidence should use moderate compression
        assert ratio == 0.25

    def test_large_workspace_adjustment(self):
        """Test ratio adjustment for large workspace."""
        classification = TaskClassification(
            primary_type="implementation",
            confidence=0.9,
        )
        features = TaskFeatures(
            total_workspace_size=2_000_000,  # 2MB
        )

        ratio = select_compression_ratio(classification, features)

        # Large workspace should compress more
        assert ratio < 0.35


class TestRationaleGeneration:
    """Test rationale generation."""

    def test_diagnostic_rationale(self):
        """Test rationale for diagnostic task."""
        classification = TaskClassification(
            primary_type="diagnostic",
            confidence=0.85,
        )
        features = TaskFeatures(
            files_to_modify=1,
        )
        ratio = 0.12

        rationale = generate_rationale(classification, features, ratio)

        assert "diagnostic" in rationale
        assert "0.85" in rationale
        assert "0.12" in rationale
        assert "reduction" in rationale.lower()

    def test_implementation_with_reference_rationale(self):
        """Test rationale for implementation with reference."""
        classification = TaskClassification(
            primary_type="implementation",
            confidence=0.90,
        )
        features = TaskFeatures(
            new_files_count=5,
            has_reference_impl=True,
        )
        ratio = 0.40

        rationale = generate_rationale(classification, features, ratio)

        assert "implementation" in rationale
        assert "new files" in rationale.lower()
        assert "reference" in rationale.lower()
        assert "0.40" in rationale
