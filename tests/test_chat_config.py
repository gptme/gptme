from dataclasses import replace as dc_replace
from pathlib import Path

import tomlkit

from gptme.config import ChatConfig


def test_chat_config_from_logdir(tmp_path: Path):
    """Test loading ChatConfig from a log directory."""
    config = ChatConfig(_logdir=tmp_path, model="test-model")
    config.save()
    loaded = ChatConfig.from_logdir(tmp_path)
    assert loaded.model == "test-model"


def test_chat_config_load_or_create(tmp_path: Path):
    """Test loading or creating ChatConfig with CLI overrides."""
    # Test with no existing config
    cli_config = ChatConfig(model="cli-model", stream=False)
    config = ChatConfig.load_or_create(tmp_path, cli_config).save()
    assert config.model == "cli-model"
    assert config.stream is False
    assert config.interactive is True  # default value preserved

    # Test with existing config
    existing = ChatConfig(_logdir=tmp_path, model="existing-model", interactive=False)
    existing.save()

    # CLI overrides should take precedence
    config = ChatConfig.load_or_create(tmp_path, cli_config).save()
    assert config.model == "cli-model"  # CLI value
    assert config.stream is False  # CLI value
    assert config.interactive is False  # existing value preserved

    # Values equal to defaults should not override existing config
    # stream=True is default
    cli_config_with_defaults = ChatConfig(model="cli-model", stream=True)
    config = ChatConfig.load_or_create(tmp_path, cli_config_with_defaults).save()
    assert config.model == "cli-model"  # CLI value
    assert (
        config.stream is False
    )  # existing value preserved (not overridden by default)
    assert config.interactive is False  # existing value preserved


def test_chat_config_save_preserves_formatting(tmp_path: Path):
    """Test that saving a config preserves TOML comments and formatting."""
    config_path = tmp_path / "config.toml"

    # Write a config with comments
    config_path.write_text(
        "# Chat session configuration\n"
        "[chat]\n"
        "# The model to use for this conversation\n"
        'model = "openai/gpt-4o"\n'
        "stream = true\n"
        "\n"
        "[env]\n"
    )

    # Load, modify, and save
    config = ChatConfig.from_logdir(tmp_path)
    assert config.model == "openai/gpt-4o"

    # Update model and save
    config = dc_replace(config, model="anthropic/claude-sonnet-4-5-20250514")
    config.save()

    # Verify comments are preserved
    saved = config_path.read_text()
    assert "# Chat session configuration" in saved
    assert "# The model to use for this conversation" in saved
    assert 'model = "anthropic/claude-sonnet-4-5-20250514"' in saved


def test_chat_config_save_roundtrip(tmp_path: Path):
    """Test that save/load roundtrip produces valid TOML."""
    config = ChatConfig(
        _logdir=tmp_path,
        model="test/model",
        stream=False,
        tools=["shell", "python"],
    )
    config.save()

    # Verify file is valid TOML
    config_path = tmp_path / "config.toml"
    with open(config_path) as f:
        data = tomlkit.load(f).unwrap()
    assert data["chat"]["model"] == "test/model"
    assert data["chat"]["stream"] is False
    assert data["chat"]["tools"] == ["shell", "python"]

    # Load back and verify
    loaded = ChatConfig.from_logdir(tmp_path)
    assert loaded.model == "test/model"
    assert loaded.stream is False
    assert loaded.tools == ["shell", "python"]


def test_chat_config_save_new_section_uses_header(tmp_path: Path):
    """Test that a new dict section added to an existing config uses a proper [section] header."""
    config_path = tmp_path / "config.toml"

    # Write a config that only has [chat] — no [env] section
    config_path.write_text('[chat]\nmodel = "test/model"\n')

    # Save a config that adds an env section for the first time
    config = ChatConfig(_logdir=tmp_path, model="test/model", env={"MY_VAR": "hello"})
    config.save()

    saved = config_path.read_text()
    # Should serialize as [env] header, not inline: env = {MY_VAR = "hello"}
    assert "[env]" in saved, f"Expected [env] section header, got:\n{saved}"
