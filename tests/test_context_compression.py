"""Unit tests for context compression components."""

import pytest

from gptme.context_compression.compressor import CompressionResult, ContextCompressor
from gptme.context_compression.config import CompressionConfig
from gptme.context_compression.extractive import ExtractiveSummarizer


class TestCompressionConfig:
    """Tests for CompressionConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CompressionConfig()
        assert config.enabled is False
        assert config.compressor == "extractive"
        assert config.target_ratio == 0.7
        assert config.min_section_length == 100
        assert config.preserve_code is True
        assert config.preserve_headings is True
        assert config.embedding_model == "all-MiniLM-L6-v2"

    def test_custom_config(self):
        """Test creating config with custom values."""
        config = CompressionConfig(
            enabled=True,
            compressor="llmlingua",
            target_ratio=0.5,
            min_section_length=200,
            preserve_code=False,
            preserve_headings=False,
            embedding_model="custom-model",
        )
        assert config.enabled is True
        assert config.compressor == "llmlingua"
        assert config.target_ratio == 0.5
        assert config.min_section_length == 200
        assert config.preserve_code is False
        assert config.preserve_headings is False
        assert config.embedding_model == "custom-model"

    def test_from_dict(self):
        """Test creating config from dictionary."""
        config_dict = {
            "enabled": True,
            "target_ratio": 0.6,
            "min_section_length": 150,
            "unknown_field": "ignored",  # Should be ignored
        }
        config = CompressionConfig.from_dict(config_dict)
        assert config.enabled is True
        assert config.target_ratio == 0.6
        assert config.min_section_length == 150
        # Unknown fields are ignored, defaults used for others
        assert config.compressor == "extractive"

    def test_from_dict_empty(self):
        """Test creating config from empty dictionary uses defaults."""
        config = CompressionConfig.from_dict({})
        assert config.enabled is False
        assert config.target_ratio == 0.7


class TestCompressionResult:
    """Tests for CompressionResult dataclass."""

    def test_compression_result_creation(self):
        """Test creating a compression result."""
        result = CompressionResult(
            compressed="Short text",
            original_length=100,
            compressed_length=50,
            compression_ratio=0.5,
        )
        assert result.compressed == "Short text"
        assert result.original_length == 100
        assert result.compressed_length == 50
        assert result.compression_ratio == 0.5

    def test_compression_ratio_calculation(self):
        """Test compression ratio represents size relationship."""
        # 50% compression
        result1 = CompressionResult(
            compressed="Half",
            original_length=100,
            compressed_length=50,
            compression_ratio=0.5,
        )
        assert result1.compression_ratio == 0.5

        # No compression (ratio = 1.0)
        result2 = CompressionResult(
            compressed="Same",
            original_length=100,
            compressed_length=100,
            compression_ratio=1.0,
        )
        assert result2.compression_ratio == 1.0


class TestExtractiveSummarizer:
    """Tests for ExtractiveSummarizer implementation."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return CompressionConfig(
            enabled=True,
            target_ratio=0.7,
            min_section_length=50,
            preserve_code=True,
            preserve_headings=True,
        )

    @pytest.fixture
    def summarizer(self, config):
        """Create ExtractiveSummarizer instance."""
        return ExtractiveSummarizer(config)

    def test_initialization(self, config):
        """Test summarizer initialization."""
        summarizer = ExtractiveSummarizer(config)
        assert summarizer.config == config
        assert summarizer._embedder is None  # Lazy loaded

    def test_split_sentences_basic(self, summarizer):
        """Test basic sentence splitting."""
        text = "First sentence. Second sentence! Third sentence?"
        sentences = summarizer._split_sentences(text)
        assert len(sentences) == 3
        assert sentences[0] == "First sentence."
        assert sentences[1] == "Second sentence!"
        assert sentences[2] == "Third sentence?"

    def test_split_sentences_empty(self, summarizer):
        """Test splitting empty text."""
        sentences = summarizer._split_sentences("")
        assert len(sentences) == 0

    def test_split_sentences_single(self, summarizer):
        """Test splitting single sentence."""
        text = "Only one sentence."
        sentences = summarizer._split_sentences(text)
        assert len(sentences) == 1
        assert sentences[0] == "Only one sentence."

    def test_split_sentences_multiline(self, summarizer):
        """Test splitting multiline text."""
        text = "First line.\nSecond line! Another sentence. Final line?"
        sentences = summarizer._split_sentences(text)
        assert len(sentences) == 4

    def test_preserve_structure_code_blocks(self, summarizer):
        """Test preserving code blocks."""
        text = """
Some text before.
```python
def hello():
    print("world")
```
Some text after.
"""
        preserved, positions = summarizer._preserve_structure(text)
        assert len(preserved) == 1
        assert "```python" in preserved[0]
        assert "def hello():" in preserved[0]
        assert len(positions) == 1
        assert positions[0] > 0

    def test_preserve_structure_headings(self, summarizer):
        """Test preserving markdown headings."""
        text = """
# Main Title
Some content here.
## Subsection
More content.
### Detail
Final content.
"""
        preserved, positions = summarizer._preserve_structure(text)
        assert len(preserved) == 3
        assert "# Main Title" in preserved[0]
        assert "## Subsection" in preserved[1]
        assert "### Detail" in preserved[2]
        assert len(positions) == 3

    def test_preserve_structure_mixed(self, summarizer):
        """Test preserving both code blocks and headings."""
        text = """
# Introduction
Regular text.
```javascript
const x = 42;
```
## Details
More text.
"""
        preserved, positions = summarizer._preserve_structure(text)
        # Should find 1 code block and 2 headings
        assert len(preserved) == 3
        assert any("# Introduction" in item for item in preserved)
        assert any("```javascript" in item for item in preserved)
        assert any("## Details" in item for item in preserved)

    def test_preserve_structure_none(self, summarizer):
        """Test with no preservable elements."""
        text = "Just plain text without any special formatting."
        preserved, positions = summarizer._preserve_structure(text)
        assert len(preserved) == 0
        assert len(positions) == 0

    def test_preserve_structure_disabled(self):
        """Test preservation when disabled in config."""
        config = CompressionConfig(
            preserve_code=False,
            preserve_headings=False,
        )
        summarizer = ExtractiveSummarizer(config)
        text = """
# Heading
```python
code()
```
"""
        preserved, positions = summarizer._preserve_structure(text)
        assert len(preserved) == 0
        assert len(positions) == 0

    def test_get_embedder_import_error(self, summarizer, monkeypatch):
        """Test graceful handling of missing sentence-transformers."""
        # Mock the import to raise ImportError
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("sentence-transformers not available")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        # Clear cached embedder
        summarizer._embedder = None

        # Should raise ImportError with helpful message
        with pytest.raises(ImportError) as exc_info:
            summarizer._get_embedder()

        assert "sentence-transformers not installed" in str(exc_info.value)
        assert "pip install sentence-transformers" in str(exc_info.value)

    def test_score_sentences_without_context(self, summarizer):
        """Test sentence scoring without context (length-based)."""
        sentences = [
            "Short.",
            "Medium length sentence.",
            "Very long sentence with more words.",
        ]
        scores = summarizer._score_sentences(sentences, context="")

        # Scores should be based on length
        assert len(scores) == 3
        assert all(0.0 <= score <= 1.0 for score in scores)
        # Longest sentence should have highest score
        assert scores[2] > scores[1] > scores[0]

    def test_score_sentences_with_context(self, summarizer):
        """Test sentence scoring with context (requires embedding model)."""
        sentences = [
            "The weather is nice today.",
            "Python is a programming language.",
            "Machine learning uses neural networks.",
        ]
        context = "Tell me about Python programming"

        try:
            scores = summarizer._score_sentences(sentences, context)
            assert len(scores) == 3
            assert all(isinstance(score, float) for score in scores)
            # Python-related sentence should score higher with Python context
            # (This may not always be true due to embedding model variations)
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_select_sentences_basic(self, summarizer):
        """Test sentence selection maintains order."""
        sentences = ["First.", "Second.", "Third.", "Fourth."]
        scores = [0.5, 0.9, 0.3, 0.8]  # Second and Fourth highest

        selected = summarizer._select_sentences(sentences, scores, target_count=2)
        # Should maintain original order: indices 1 and 3
        assert selected == [1, 3]

    def test_select_sentences_all(self, summarizer):
        """Test selecting all sentences."""
        sentences = ["A.", "B.", "C."]
        scores = [0.5, 0.7, 0.3]

        selected = summarizer._select_sentences(sentences, scores, target_count=3)
        assert selected == [0, 1, 2]

    def test_select_sentences_one(self, summarizer):
        """Test selecting single sentence."""
        sentences = ["Low.", "High.", "Medium."]
        scores = [0.2, 0.9, 0.5]

        selected = summarizer._select_sentences(sentences, scores, target_count=1)
        assert selected == [1]  # Highest score

    def test_compress_short_content(self, summarizer):
        """Test compression skips short content."""
        short_text = "Too short to compress."
        result = summarizer.compress(short_text)

        assert result.compressed == short_text
        assert result.original_length == len(short_text)
        assert result.compressed_length == len(short_text)
        assert result.compression_ratio == 1.0

    def test_compress_basic(self, summarizer):
        """Test basic compression flow."""
        text = (
            "First sentence with content. "
            "Second sentence with more information. "
            "Third sentence adds details. "
            "Fourth sentence provides context. "
            "Fifth sentence concludes topic."
        )

        result = summarizer.compress(text, target_ratio=0.6)

        assert result.original_length == len(text)
        assert result.compressed_length < result.original_length
        assert result.compressed_length == len(result.compressed)
        assert 0.0 < result.compression_ratio <= 1.0
        # Should select ~60% of sentences (3 out of 5)
        assert "sentence" in result.compressed.lower()

    def test_compress_with_context(self, summarizer):
        """Test compression with context for relevance scoring."""
        text = (
            "Python is great for scripting. "
            "JavaScript runs in browsers. "
            "Python has excellent libraries. "
            "CSS styles web pages. "
            "Python's syntax is clean."
        )
        context = "Tell me about Python programming"

        try:
            result = summarizer.compress(text, target_ratio=0.6, context=context)

            assert result.compressed_length < result.original_length
            # Python-related sentences should be preferred
            assert "Python" in result.compressed
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_compress_empty_text(self, summarizer):
        """Test compression of empty text."""
        result = summarizer.compress("")

        assert result.compressed == ""
        assert result.original_length == 0
        assert result.compressed_length == 0
        assert result.compression_ratio == 1.0

    def test_compress_single_sentence(self, summarizer):
        """Test compression of single sentence."""
        text = "This is a single sentence that meets minimum length requirements for compression testing."
        result = summarizer.compress(text, target_ratio=0.5)

        # Single sentence should be kept
        assert result.compressed == text
        assert result.compression_ratio == 1.0

    def test_compress_target_ratio_variations(self, summarizer):
        """Test different target ratios."""
        text = " ".join([f"Sentence number {i}." for i in range(10)])

        # High ratio (keep more)
        result_high = summarizer.compress(text, target_ratio=0.9)

        # Low ratio (keep less)
        result_low = summarizer.compress(text, target_ratio=0.3)

        assert result_high.compressed_length > result_low.compressed_length
        assert result_high.compression_ratio > result_low.compression_ratio

    def test_compress_preserves_code_blocks(self, summarizer):
        """Test that code blocks are preserved during compression."""
        text = """
This is some introductory text that provides context.
Here is more information about the topic.
Additional details are provided in this sentence.

```python
def important_function():
    return "This code must be preserved"
```

Some concluding text here.
More sentences to make the text longer.
Final sentence of the document.
"""
        result = summarizer.compress(text, target_ratio=0.5)

        # Code block should be preserved completely
        assert "```python" in result.compressed
        assert "def important_function():" in result.compressed
        assert 'return "This code must be preserved"' in result.compressed
        assert "```" in result.compressed

        # Verify code block appears exactly once (no duplication)
        assert result.compressed.count("```python") == 1
        assert result.compressed.count("def important_function():") == 1

    def test_compress_preserves_headings(self, summarizer):
        """Test that markdown headings are preserved during compression."""
        text = """
# Main Title

This is introductory text for the main section.
Here is additional context and information.

## Important Subsection

Details about the subsection go here.
More information is provided in these sentences.

### Detail Level

Final details and conclusions.
Additional information here.
"""
        result = summarizer.compress(text, target_ratio=0.5)

        # Headings should be preserved
        assert "# Main Title" in result.compressed
        assert "## Important Subsection" in result.compressed
        assert "### Detail Level" in result.compressed

        # Verify headings appear exactly once (no duplication)
        assert result.compressed.count("# Main Title") == 1
        assert result.compressed.count("## Important Subsection") == 1
        assert result.compressed.count("### Detail Level") == 1

    def test_compress_preserves_mixed_content(self, summarizer):
        """Test preservation of both code blocks and headings together."""
        text = """
# Introduction

This section introduces the concept with some text.
More introductory information is provided here.

```javascript
const data = {
    key: "value"
};
```

## Implementation Details

The implementation uses several sentences for description.
Additional technical details are included here.

```python
def process():
    pass
```

### Summary

Final thoughts and conclusions go here.
"""
        result = summarizer.compress(text, target_ratio=0.6)

        # Both code blocks should be preserved
        assert "```javascript" in result.compressed
        assert 'key: "value"' in result.compressed
        assert "```python" in result.compressed
        assert "def process():" in result.compressed

        # All headings should be preserved
        assert "# Introduction" in result.compressed
        assert "## Implementation Details" in result.compressed
        assert "### Summary" in result.compressed

        # Verify no duplication
        assert result.compressed.count("```javascript") == 1
        assert result.compressed.count("```python") == 1
        assert result.compressed.count("# Introduction") == 1

    def test_compress_no_expansion(self, summarizer):
        """Test that compression never expands content."""
        text = """
# Document Title

This is a paragraph with multiple sentences for testing.
The compression system should reduce or maintain size.

```python
def function():
    return True
```

## Section Two

More content goes here with additional sentences.
The final result should not be larger than input.
"""
        result = summarizer.compress(text, target_ratio=0.7)

        # Compressed content should never be larger than original
        assert result.compressed_length <= result.original_length
        assert result.compression_ratio <= 1.0

    def test_compress_preserves_multiline_code(self, summarizer):
        """Test that multiline code blocks are fully preserved."""
        text = """
Some text before the code.
Additional context is provided here.

```python
class Example:
    def __init__(self):
        self.value = 42

    def method(self):
        return self.value * 2
```

Text after the code block.
More sentences for compression.
"""
        result = summarizer.compress(text, target_ratio=0.5)

        # Entire code block should be preserved
        assert "class Example:" in result.compressed
        assert "def __init__(self):" in result.compressed
        assert "self.value = 42" in result.compressed
        assert "def method(self):" in result.compressed
        assert "return self.value * 2" in result.compressed

        # No duplication
        assert result.compressed.count("class Example:") == 1

    def test_compress_preservation_disabled(self):
        """Test that preservation can be disabled."""
        config = CompressionConfig(
            enabled=True,
            preserve_code=False,
            preserve_headings=False,
            min_section_length=50,
        )
        summarizer = ExtractiveSummarizer(config)

        text = """
# Heading

Some text here.

```python
code()
```

More text.
"""
        result = summarizer.compress(text, target_ratio=0.5)

        # With preservation disabled, code blocks and headings might not be preserved
        # (they're treated as regular text and may be removed)
        # We just verify it doesn't crash and produces valid output
        assert isinstance(result.compressed, str)
        assert result.compression_ratio <= 1.0


class TestContextCompressorInterface:
    """Tests for ContextCompressor abstract base class."""

    def test_abstract_interface(self):
        """Test that ContextCompressor cannot be instantiated directly."""
        with pytest.raises(TypeError):
            # Abstract class cannot be instantiated
            ContextCompressor()  # type: ignore

    def test_interface_implementation(self):
        """Test that ExtractiveSummarizer implements the interface."""
        config = CompressionConfig()
        summarizer = ExtractiveSummarizer(config)

        # Should have compress method
        assert hasattr(summarizer, "compress")
        assert callable(summarizer.compress)


class TestIntegrationScenarios:
    """Integration tests for realistic usage scenarios."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return CompressionConfig(
            enabled=True,
            target_ratio=0.7,
            min_section_length=50,
        )

    @pytest.fixture
    def summarizer(self, config):
        """Create ExtractiveSummarizer instance."""
        return ExtractiveSummarizer(config)

    def test_markdown_content(self, summarizer):
        """Test compression of markdown-style content."""
        markdown = """
# Introduction

This is the introduction paragraph. It contains important information about the topic.

## Details

Here are some details about the implementation. The system works by processing text.
Additional information is provided here. More context follows.

## Conclusion

The conclusion summarizes the key points. Final thoughts are included here.
"""
        result = summarizer.compress(markdown, target_ratio=0.6)

        assert result.compressed_length < result.original_length
        assert result.compressed_length > 0
        assert isinstance(result.compressed, str)

    def test_code_documentation(self, summarizer):
        """Test compression of code documentation."""
        doc = """
The function takes two parameters and returns a value.
It performs validation on the input data.
Error handling is implemented for edge cases.
The algorithm has O(n) time complexity.
Unit tests verify correct behavior.
Integration tests check end-to-end functionality.
"""
        result = summarizer.compress(doc, target_ratio=0.5)

        assert result.compression_ratio <= 0.7  # Some compression achieved
        assert len(result.compressed) > 0

    def test_conversation_context(self, summarizer):
        """Test compression of conversation history."""
        conversation = """
User asked about Python programming.
Assistant explained Python basics.
User requested examples of list comprehensions.
Assistant provided detailed examples.
User asked about performance considerations.
Assistant discussed optimization techniques.
"""
        context = "Python list comprehensions"

        try:
            result = summarizer.compress(
                conversation, target_ratio=0.6, context=context
            )

            # Should prefer sentences related to context
            assert (
                "list comprehensions" in result.compressed.lower()
                or "examples" in result.compressed.lower()
            )
        except ImportError:
            pytest.skip("sentence-transformers not installed")
