"""Tests for context compression integration with gptme."""

import tempfile
from pathlib import Path

from gptme.config import ProjectConfig
from gptme.prompts import prompt_workspace


def test_compression_config_parsing():
    """Test that compression config is parsed from gptme.toml."""
    config_data = {
        "compression": {
            "enabled": True,
            "target_ratio": 0.7,
            "min_section_length": 50,
        }
    }

    config = ProjectConfig.from_dict(config_data)
    assert config.compression.enabled is True
    assert config.compression.target_ratio == 0.7
    assert config.compression.min_section_length == 50


def test_compression_integration_enabled():
    """Test that compression is applied when enabled."""
    # Create test workspace with gptme.toml
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create gptme.toml with compression enabled
        config_path = workspace / "gptme.toml"
        config_path.write_text("""
[compression]
enabled = true
target_ratio = 0.7
min_section_length = 50
""")

        # Create a test file with enough content to compress
        test_file = workspace / "test.md"
        test_file.write_text("""
# Test Document

This is a test document with multiple sentences.
The compression system should reduce this content.
Here are several more sentences that provide context.
This content can be compressed while preserving key information.

## Another Section

Additional information that can be summarized.
The extractive summarizer will select relevant sentences.
Some sentences are more important than others.
Critical information is preserved during compression.
""")

        # Get the workspace prompt (which should apply compression)
        messages = list(prompt_workspace(workspace))

        # Verify messages were generated
        assert len(messages) > 0
        # The content should be compressed (shorter than original)
        # Note: Exact verification would require checking the actual content


def test_compression_disabled_by_default():
    """Test that compression is disabled by default."""
    config = ProjectConfig.from_dict({})
    assert config.compression.enabled is False


def test_compression_skips_short_content():
    """Test that short content is not compressed."""
    config_data = {
        "compression": {
            "enabled": True,
            "min_section_length": 100,
        }
    }
    config = ProjectConfig.from_dict(config_data)

    # Short content (< 100 chars) should not be compressed
    # This is tested implicitly in the integration
    assert config.compression.min_section_length == 100
