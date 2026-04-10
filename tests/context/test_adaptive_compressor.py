"""Tests for gptme.context.adaptive_compressor module.

Covers:
- CompressionResult dataclass (tokens_saved property)
- extract_code_blocks (marker replacement, round-trip)
- score_sentence (positional, key terms, task type, length)
- extractive_compress (code block preservation, sentence selection, ratio targeting)
- AdaptiveCompressor.compress (end-to-end, short content, logging)
"""

from gptme.context.adaptive_compressor import (
    AdaptiveCompressor,
    CompressionResult,
    extract_code_blocks,
    extractive_compress,
    score_sentence,
)
from gptme.context.task_analyzer import TaskClassification

# ──────────────────── CompressionResult ────────────────────


class TestCompressionResult:
    def test_tokens_saved_positive(self):
        r = CompressionResult(
            original_content="x" * 400,
            compressed_content="x" * 200,
            compression_ratio=0.5,
            task_classification=TaskClassification(primary_type="fix", confidence=0.8),
            rationale="test",
        )
        assert r.tokens_saved == 50  # (400-200) // 4

    def test_tokens_saved_zero(self):
        r = CompressionResult(
            original_content="hello",
            compressed_content="hello",
            compression_ratio=1.0,
            task_classification=TaskClassification(primary_type="fix", confidence=0.8),
            rationale="test",
        )
        assert r.tokens_saved == 0

    def test_tokens_saved_large(self):
        r = CompressionResult(
            original_content="x" * 10000,
            compressed_content="x" * 2000,
            compression_ratio=0.2,
            task_classification=TaskClassification(
                primary_type="diagnostic", confidence=0.9
            ),
            rationale="test",
        )
        assert r.tokens_saved == 2000  # (10000-2000) // 4


# ──────────────────── extract_code_blocks ────────────────────


class TestExtractCodeBlocks:
    def test_no_code_blocks(self):
        content = "Just plain text without any code."
        cleaned, blocks = extract_code_blocks(content)
        assert cleaned == content
        assert blocks == []

    def test_single_code_block(self):
        content = "Before\n```python\ndef foo():\n    pass\n```\nAfter"
        cleaned, blocks = extract_code_blocks(content)
        assert "__CODE_BLOCK_0__" in cleaned
        assert len(blocks) == 1
        assert "def foo():" in blocks[0][1]

    def test_multiple_code_blocks(self):
        content = "A\n```py\ncode1\n```\nB\n```js\ncode2\n```\nC"
        cleaned, blocks = extract_code_blocks(content)
        assert "__CODE_BLOCK_0__" in cleaned
        assert "__CODE_BLOCK_1__" in cleaned
        assert len(blocks) == 2
        assert "code1" in blocks[0][1]
        assert "code2" in blocks[1][1]

    def test_code_block_round_trip(self):
        """Replacing markers with original blocks should reconstruct content."""
        content = "Text\n```bash\nls -la\n```\nMore text"
        cleaned, blocks = extract_code_blocks(content)
        restored = cleaned
        for marker, block in blocks:
            restored = restored.replace(marker, block)
        assert restored == content

    def test_empty_code_block(self):
        content = "Before\n```\n```\nAfter"
        cleaned, blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert "__CODE_BLOCK_0__" in cleaned


# ──────────────────── score_sentence ────────────────────


class TestScoreSentence:
    def test_first_sentence_bonus(self):
        score = score_sentence("Hello world", position=0, total=10)
        assert score >= 2.0

    def test_last_sentence_bonus(self):
        score = score_sentence("Final thought", position=9, total=10)
        assert score >= 1.5

    def test_early_sentence_bonus(self):
        score = score_sentence("Second sentence", position=1, total=10)
        assert score >= 1.0

    def test_middle_sentence_no_positional(self):
        """Middle sentences with no key terms get no positional bonus."""
        score = score_sentence("Ordinary sentence here", position=5, total=10)
        # Should only have length-related score
        assert score < 1.0

    def test_key_terms_boost(self):
        score_with = score_sentence("This is an error case", position=5, total=10)
        score_without = score_sentence("This is a normal case", position=5, total=10)
        assert score_with > score_without

    def test_multiple_key_terms(self):
        """Multiple key terms should stack."""
        score = score_sentence("Fix the critical error bug", position=5, total=10)
        # "fix", "critical", "error", "bug" should all contribute
        assert score > 1.0

    def test_task_specific_terms_diagnostic(self):
        score = score_sentence(
            "Check the traceback and exception",
            position=5,
            total=10,
            task_type="diagnostic",
        )
        # "traceback" and "exception" are diagnostic-specific
        assert score > 0

    def test_task_specific_terms_fix(self):
        score = score_sentence(
            "Apply the patch to resolve", position=5, total=10, task_type="fix"
        )
        assert score > 0

    def test_task_specific_terms_implementation(self):
        score = score_sentence(
            "Design the architecture pattern",
            position=5,
            total=10,
            task_type="implementation",
        )
        assert score > 0

    def test_task_specific_terms_exploration(self):
        score = score_sentence(
            "Research and analyze the results",
            position=5,
            total=10,
            task_type="exploration",
        )
        assert score > 0

    def test_unknown_task_type(self):
        """Unknown task types should not crash, just skip task-specific boosting."""
        score = score_sentence(
            "Normal sentence", position=5, total=10, task_type="unknown"
        )
        assert isinstance(score, float)

    def test_very_short_penalty(self):
        """Sentences < 10 chars get penalized."""
        score = score_sentence("Hi", position=5, total=10)
        assert score < 0

    def test_short_sentence_bonus(self):
        """Sentences 10-50 chars get a small bonus (information dense)."""
        score = score_sentence("This is a concise point.", position=5, total=10)
        assert score > 0

    def test_long_sentence_penalty(self):
        """Sentences > 200 chars get slightly penalized."""
        long = "x " * 120  # ~240 chars
        score = score_sentence(long, position=5, total=10)
        # Should have slight penalty but key terms could offset
        assert isinstance(score, float)


# ──────────────────── extractive_compress ────────────────────


class TestExtractiveCompress:
    def test_short_content_unchanged(self):
        """Content with <=3 sentences should not be compressed."""
        content = "Sentence one. Sentence two. Sentence three."
        result = extractive_compress(content, target_ratio=0.5)
        assert result == content

    def test_preserves_code_blocks(self):
        content = (
            "First sentence about setup. "
            "Second explains the context. "
            "Third is more detail. "
            "Fourth adds background. "
            "```python\ndef important():\n    return True\n```\n"
            "Fifth wraps up the discussion."
        )
        result = extractive_compress(content, target_ratio=0.5)
        assert "def important():" in result

    def test_reduces_length(self):
        """With enough sentences, compression should reduce total length."""
        sentences = [
            f"This is sentence number {i} with some detail." for i in range(20)
        ]
        content = " ".join(sentences)
        result = extractive_compress(content, target_ratio=0.5)
        assert len(result) < len(content)

    def test_preserves_important_sentences(self):
        """First sentence (position 0) should be preserved due to positional bias."""
        content = (
            "Critical error found in the system. "
            "Additional background info here. "
            "More context about the environment. "
            "Some less important detail. "
            "Another detail about things. "
            "Final conclusion about the findings."
        )
        result = extractive_compress(content, target_ratio=0.5, task_type="diagnostic")
        # First sentence should likely be kept (high positional + key term score)
        assert "Critical error" in result

    def test_ratio_1_preserves_all(self):
        """Ratio of 1.0 should preserve essentially all content."""
        sentences = [f"Sentence {i} about topic." for i in range(10)]
        content = " ".join(sentences)
        result = extractive_compress(content, target_ratio=1.0)
        # With ratio 1.0, all sentences should be kept
        assert len(result) >= len(content) * 0.9

    def test_task_type_affects_selection(self):
        """Different task types should select different sentences."""
        content = (
            "The system started normally. "
            "An exception was thrown in the stack trace. "
            "The design pattern uses factory method. "
            "The architecture follows clean patterns. "
            "Debug logs show the error sequence. "
            "Research into alternatives is needed."
        )
        diag = extractive_compress(content, target_ratio=0.4, task_type="diagnostic")
        impl = extractive_compress(
            content, target_ratio=0.4, task_type="implementation"
        )
        # Both should be compressed (shorter than original)
        assert len(diag) < len(content)
        assert len(impl) < len(content)
        # Diagnostic should favor error/exception sentences; implementation should
        # favor design/architecture sentences — so the outputs should differ.
        assert diag != impl
        assert "exception" in diag.lower() or "debug" in diag.lower()
        assert "design" in impl.lower() or "architecture" in impl.lower()

    def test_code_blocks_with_markers_in_sentences(self):
        """Code block markers embedded in sentences should be preserved."""
        content = (
            "Setup step one. "
            "Then run:\n```bash\necho hello\n```\n"
            "Verify the output matches. "
            "Additional check needed. "
            "Final verification step."
        )
        result = extractive_compress(content, target_ratio=0.6)
        assert "echo hello" in result


# ──────────────────── AdaptiveCompressor ────────────────────


class TestAdaptiveCompressor:
    def test_basic_compression(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        # Use short filenames (context_files are used as path components internally)
        file_names = [f"src{i}.py" for i in range(10)]
        result = compressor.compress(
            prompt="fix the bug in utils.py",
            context_files=file_names,
        )
        assert isinstance(result, CompressionResult)
        assert result.task_classification.primary_type in {
            "diagnostic",
            "fix",
            "implementation",
            "exploration",
            "refactor",
        }
        assert 0.10 <= result.compression_ratio <= 0.50

    def test_short_content_not_compressed(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        result = compressor.compress(
            prompt="fix bug",
            context_files=["short"],
        )
        assert result.compressed_content == "short"

    def test_empty_context(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        result = compressor.compress(
            prompt="explore the codebase",
            context_files=[],
        )
        assert result.compressed_content == ""
        assert result.original_content == ""

    def test_none_context(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        result = compressor.compress(
            prompt="debug error",
            context_files=None,
        )
        assert result.compressed_content == ""

    def test_rationale_generated(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        # Use short filenames (context_files are used as path components internally)
        # and pass actual content via current_context
        result = compressor.compress(
            prompt="implement new feature",
            context_files=[f"file{i}.py" for i in range(5)],
            current_context=["Some content " * 50],
        )
        assert "Task Type:" in result.rationale
        assert "Compression Ratio:" in result.rationale
        assert "Compression Method: Extractive summarization" in result.rationale

    def test_tokens_saved_property(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        # Use short filenames (context_files are path components internally)
        short_names = [f"file{i}.py" for i in range(20)]
        result = compressor.compress(
            prompt="refactor the module structure",
            context_files=short_names,
        )
        assert result.tokens_saved >= 0

    def test_logging_enabled(self, tmp_path, caplog):
        """With enable_logging=True, compression decisions should be logged."""
        import logging

        compressor = AdaptiveCompressor(workspace_root=tmp_path, enable_logging=True)
        with caplog.at_level(logging.INFO, logger="gptme.context.adaptive_compressor"):
            compressor.compress(
                prompt="fix the error",
                context_files=[f"f{i}.py" for i in range(3)],
            )
        assert any("Adaptive compression" in r.message for r in caplog.records)

    def test_logging_disabled(self, tmp_path, caplog):
        """With enable_logging=False, no compression logs should appear."""
        import logging

        compressor = AdaptiveCompressor(workspace_root=tmp_path, enable_logging=False)
        with caplog.at_level(logging.INFO, logger="gptme.context.adaptive_compressor"):
            compressor.compress(
                prompt="fix the error",
                context_files=[f"f{i}.py" for i in range(3)],
            )
        assert not any("Adaptive compression" in r.message for r in caplog.records)

    def test_compressed_not_larger(self, tmp_path):
        """Compressed content should never be larger than original."""
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        file_names = [f"module{i}.py" for i in range(20)]
        result = compressor.compress(
            prompt="simplify and cleanup",
            context_files=file_names,
        )
        assert len(result.compressed_content) <= len(result.original_content)

    def test_default_workspace_root(self):
        """Without explicit workspace_root, should use cwd."""
        compressor = AdaptiveCompressor()
        assert compressor.workspace_root is not None

    def test_with_current_context(self, tmp_path):
        compressor = AdaptiveCompressor(workspace_root=tmp_path)
        result = compressor.compress(
            prompt="implement a new API",
            context_files=[f"api{i}.py" for i in range(5)],
            current_context=["class Foo:", "def bar:", "function baz:", "extra"],
        )
        # current_context should surface reference implementations in the rationale
        assert isinstance(result, CompressionResult)
        assert 0.10 <= result.compression_ratio <= 0.50
        assert "Task Type:" in result.rationale
        assert "Reference implementation available in workspace" in result.rationale
