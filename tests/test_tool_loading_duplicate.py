"""Test that tools don't get loaded multiple times."""

import logging

from gptme.tools import init_tools, get_tools, _get_loaded_tools


def test_init_tools_idempotent(caplog):
    """Test that calling init_tools multiple times doesn't reload tools."""
    # Clear any previously loaded tools
    _get_loaded_tools().clear()

    # Initialize tools first time
    with caplog.at_level(logging.WARNING):
        tools1 = init_tools(allowlist=["shell", "save"])

    # Verify no warnings
    assert "already loaded" not in caplog.text

    # Get loaded tools count
    loaded_count1 = len(get_tools())
    assert loaded_count1 == 2  # shell and save

    # Initialize tools second time (should be idempotent)
    with caplog.at_level(logging.WARNING):
        tools2 = init_tools(allowlist=["shell", "save"])

    # Verify no warnings about duplicate loading
    assert "already loaded" not in caplog.text

    # Verify tools count hasn't changed
    loaded_count2 = len(get_tools())
    assert loaded_count2 == loaded_count1

    # Verify same tools returned
    assert len(tools1) == len(tools2)
    assert all(t1.name == t2.name for t1, t2 in zip(tools1, tools2))


def test_init_tools_evals_scenario(caplog):
    """Test the evals scenario where init_tools is called multiple times."""
    # Clear any previously loaded tools
    _get_loaded_tools().clear()

    # Simulate running 4 evals (as in issue #403)
    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            init_tools(allowlist=["shell", "save", "patch"])

    # Verify no duplicate loading warnings
    assert "already loaded" not in caplog.text

    # Verify tools loaded only once
    loaded_tools = get_tools()
    assert len(loaded_tools) == 3  # shell, save, patch
