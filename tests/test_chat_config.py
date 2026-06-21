from dataclasses import replace as dc_replace
from pathlib import Path

import pytest
import tomlkit

from gptme.config import ChatConfig, ensure_workspace_dir, require_workspace_exists


def test_chat_config_from_logdir(tmp_path: Path):
    """Test loading ChatConfig from a log directory."""
    config = ChatConfig(_logdir=tmp_path, model="test-model")
    config.save()
    loaded = ChatConfig.from_logdir(tmp_path)
    assert loaded.model == "test-model"


def test_chat_config_from_logdir_permission_error(tmp_path: Path, monkeypatch):
    """from_logdir must not crash when workspace dir can't be created (PermissionError).

    Reproduces the gptme/gptme#2958 scenario: log dirs seeded by one user and
    then mounted read-only into a container running as a different user.
    """
    logdir = tmp_path / "conv"
    logdir.mkdir()
    # No config.toml → hits the ensure_workspace_dir branch

    def _raise(_path):
        raise PermissionError(13, "Permission denied", str(_path))

    monkeypatch.setattr(
        "gptme.config.chat.ensure_workspace_dir",
        _raise,
    )

    loaded = ChatConfig.from_logdir(logdir)
    # Conversation is still listed; workspace points to the intended path
    assert loaded.workspace == (logdir / "workspace").resolve()


def test_chat_config_from_logdir_workspace_symlink(tmp_path: Path):
    """from_logdir must not crash when 'workspace' is a pre-existing symlink.

    Some older conversations have a manually-created 'workspace' symlink
    instead of a directory. mkdir(exist_ok=True) raises FileExistsError on a
    symlink/non-dir, which previously 500'd the conversations list endpoint.
    """
    logdir = tmp_path / "conv"
    logdir.mkdir()
    target = tmp_path / "linked-workspace"
    (logdir / "workspace").symlink_to(target)  # broken symlink (target absent)

    loaded = ChatConfig.from_logdir(logdir)
    assert loaded.workspace == target


def test_ensure_workspace_dir(tmp_path: Path):
    """ensure_workspace_dir creates a missing dir but tolerates symlinks."""
    # Missing → created
    ws = tmp_path / "ws"
    ensure_workspace_dir(ws)
    assert ws.is_dir()

    # Pre-existing dir → no-op, no error
    ensure_workspace_dir(ws)

    # Broken symlink → left as-is, no FileExistsError
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")
    ensure_workspace_dir(link)
    assert link.is_symlink()
    assert not link.exists()  # target still absent — not created


def test_require_workspace_exists(tmp_path: Path):
    """require_workspace_exists raises an actionable error only when missing."""
    existing = tmp_path / "here"
    existing.mkdir()
    require_workspace_exists(existing)  # no raise

    missing = tmp_path / "gone"
    with pytest.raises(FileNotFoundError, match="workspace does not exist"):
        require_workspace_exists(missing)

    # Broken symlink counts as missing (target absent)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")
    with pytest.raises(FileNotFoundError, match="workspace does not exist"):
        require_workspace_exists(link)


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


def test_chat_config_system_prompt_roundtrip(tmp_path: Path):
    """system_prompt survives a save/load round-trip."""
    config = ChatConfig(_logdir=tmp_path, system_prompt="Answer tersely.")
    config.save()

    loaded = ChatConfig.from_logdir(tmp_path)
    assert loaded.system_prompt == "Answer tersely."


def test_chat_config_load_or_create_empty_system_prompt_clears_existing(tmp_path: Path):
    """An empty-string override clears an existing system_prompt."""
    existing = ChatConfig(_logdir=tmp_path, system_prompt="Old prompt")
    existing.save()

    cleared = ChatConfig.load_or_create(tmp_path, ChatConfig(system_prompt="")).save()
    assert cleared.system_prompt is None
    assert "system_prompt" not in cleared.to_dict()["chat"]


def test_chat_config_system_prompt_from_dict_validation(tmp_path: Path):
    """Non-string system_prompt in from_dict raises ValueError."""
    config = ChatConfig(_logdir=tmp_path)
    data = config.to_dict()
    data["chat"]["system_prompt"] = {"nested": "dict"}
    with pytest.raises(ValueError, match="chat.system_prompt must be a string"):
        ChatConfig.from_dict(data)


def test_chat_config_unknown_chat_key_raises_value_error(tmp_path: Path):
    """An unknown key under [chat] raises ValueError, not TypeError.

    Untrusted callers (the v2 conversation endpoints) only catch ValueError, so
    an unknown key must surface as a clean 400 rather than a 500 from the
    ChatConfig(**chat_data) constructor.
    """
    config = ChatConfig(_logdir=tmp_path)
    data = config.to_dict()
    data["chat"]["foobar"] = 1
    with pytest.raises(ValueError, match="Unknown keys in chat config"):
        ChatConfig.from_dict(data)
