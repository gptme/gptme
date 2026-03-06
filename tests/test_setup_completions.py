"""Tests for bash/zsh shell completion generation in setup."""

from gptme.cli.setup import _generate_click_completion


def test_generate_bash_completion():
    """Test that bash completion script is generated correctly."""
    script = _generate_click_completion("bash")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "_gptme_completion" in script
    assert "gptme" in script
    # Bash completions should have bash-specific content
    assert "complete" in script.lower()


def test_generate_zsh_completion():
    """Test that zsh completion script is generated correctly."""
    script = _generate_click_completion("zsh")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "_gptme_completion" in script
    assert "gptme" in script


def test_generate_unsupported_shell():
    """Test that unsupported shells return None."""
    result = _generate_click_completion("powershell")
    assert result is None


def test_generate_fish_completion():
    """Test that fish completion can also be generated (even though we use a separate path)."""
    script = _generate_click_completion("fish")
    assert script is not None
    assert "_GPTME_COMPLETE" in script
    assert "gptme" in script
