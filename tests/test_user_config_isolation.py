"""The shared test fixtures must never persist settings into the user's home."""

import threading
from pathlib import Path

import pytest
import tomlkit

import gptme.config.user as user_config
from gptme.config import get_config


@pytest.mark.parametrize("local", [False, True])
def test_default_config_writes_are_isolated(
    isolate_user_config: Path, local: bool
) -> None:
    # Check the boundary before writing, so a broken redirect cannot mutate HOME.
    config_file = Path(user_config.config_path)
    assert config_file == isolate_user_config
    assert config_file != Path.home() / ".config/gptme/config.toml"

    user_config.set_config_value("user.name", "isolated test", local=local)

    target = config_file.with_name("config.local.toml") if local else config_file
    assert tomlkit.parse(target.read_text(encoding="utf-8"))["user"]["name"] == (
        "isolated test"
    )
    assert get_config().user.user.name == "isolated test"


def test_background_config_writes_are_isolated(isolate_user_config: Path) -> None:
    """OAuth workers use the same disk boundary, even in a fresh context."""
    config_file = Path(user_config.config_path)
    assert config_file == isolate_user_config

    worker = threading.Thread(
        target=user_config.set_config_value,
        args=("user.name", "background test"),
    )
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert user_config.load_user_config().user.name == "background test"
