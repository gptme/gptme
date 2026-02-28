"""Tests for demo_capture.py script."""

import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import demo_capture


def test_terminal_demos_defined():
    """Verify terminal demos have required fields."""
    for demo in demo_capture.TERMINAL_DEMOS:
        assert "name" in demo
        assert "description" in demo
        assert "prompt" in demo
        assert demo["name"]  # non-empty
        assert demo["prompt"]  # non-empty


def test_webui_pages_defined():
    """Verify webui pages have required fields."""
    for page in demo_capture.WEBUI_PAGES:
        assert "name" in page
        assert "path" in page
        assert "viewport" in page
        assert "width" in page["viewport"]
        assert "height" in page["viewport"]


def test_check_prerequisites_terminal():
    """Test prerequisite checking for terminal mode."""
    with patch.object(demo_capture, "check_tool", return_value=True):
        missing = demo_capture.check_prerequisites(["terminal"])
        assert missing == []


def test_check_prerequisites_terminal_missing():
    """Test prerequisite checking when tools are missing."""
    with patch.object(demo_capture, "check_tool", return_value=False):
        missing = demo_capture.check_prerequisites(["terminal"])
        assert len(missing) == 2  # asciinema + gptme


def test_generate_summary(tmp_path):
    """Test summary generation."""
    # Create a fake file
    fake_file = tmp_path / "test.cast"
    fake_file.write_text("test content")

    results = {"terminal": [fake_file], "screenshots": []}
    summary_path = demo_capture.generate_summary(tmp_path, results)

    assert summary_path.exists()
    import json

    with open(summary_path) as f:
        data = json.load(f)

    assert "generated_at" in data
    assert "assets" in data
    assert len(data["assets"]["terminal"]) == 1
    assert data["assets"]["terminal"][0]["name"] == "test.cast"


def test_demo_names_unique():
    """Verify demo names are unique."""
    terminal_names = [d["name"] for d in demo_capture.TERMINAL_DEMOS]
    assert len(terminal_names) == len(set(terminal_names))

    webui_names = [p["name"] for p in demo_capture.WEBUI_PAGES]
    assert len(webui_names) == len(set(webui_names))
