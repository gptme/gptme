from pathlib import Path

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
