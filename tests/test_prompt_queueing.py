"""Tests for prompt queueing feature (Issue #569)."""

import sys
from unittest.mock import patch


def test_read_buffered_stdin_no_data():
    """Test that _read_buffered_stdin returns None when no data is available."""
    from gptme.chat import _read_buffered_stdin
    
    # Mock select to return empty (no data available)
    with patch('gptme.chat.select.select', return_value=([], [], [])):
        with patch('sys.stdin.isatty', return_value=True):
            result = _read_buffered_stdin()
            assert result is None


def test_read_buffered_stdin_with_data():
    """Test that _read_buffered_stdin reads buffered input."""
    from gptme.chat import _read_buffered_stdin
    
    test_input = "hello world"
    read_index = [0]
    
    def mock_read(n):
        if read_index[0] < len(test_input):
            char = test_input[read_index[0]]
            read_index[0] += 1
            return char
        return ""
    
    def mock_select(*args, **kwargs):
        # Return readable if we still have data to read
        if read_index[0] < len(test_input):
            return ([sys.stdin], [], [])
        return ([], [], [])
    
    with patch('gptme.chat.select.select', side_effect=mock_select):
        with patch('sys.stdin.isatty', return_value=True):
            with patch('sys.stdin.read', side_effect=mock_read):
                result = _read_buffered_stdin()
                assert result == "hello world"


def test_read_buffered_stdin_not_tty():
    """Test that _read_buffered_stdin returns None for non-TTY stdin."""
    from gptme.chat import _read_buffered_stdin
    
    with patch('sys.stdin.isatty', return_value=False):
        result = _read_buffered_stdin()
        assert result is None


def test_read_buffered_stdin_strips_whitespace():
    """Test that _read_buffered_stdin strips leading/trailing whitespace."""
    from gptme.chat import _read_buffered_stdin
    
    test_input = "  queued message  \n"
    read_index = [0]
    
    def mock_read(n):
        if read_index[0] < len(test_input):
            char = test_input[read_index[0]]
            read_index[0] += 1
            return char
        return ""
    
    def mock_select(*args, **kwargs):
        if read_index[0] < len(test_input):
            return ([sys.stdin], [], [])
        return ([], [], [])
    
    with patch('gptme.chat.select.select', side_effect=mock_select):
        with patch('sys.stdin.isatty', return_value=True):
            with patch('sys.stdin.read', side_effect=mock_read):
                result = _read_buffered_stdin()
                assert result == "queued message"
