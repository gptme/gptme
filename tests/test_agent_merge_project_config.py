"""Tests for gptme.toml handling when creating an agent from a template."""

from pathlib import Path

import tomlkit

from gptme.agent.workspace import _merge_project_config
from gptme.config.project import get_project_config

TEMPLATE_TOML = """\
[agent]
name = "MyAgent"

[prompt]
files = [
  "README.md",
  # "WORKFLOW.md",  # rendered at run time
]
context_cmd = "scripts/context.sh"
"""


def test_existing_config_is_preserved(tmp_path: Path):
    config_path = tmp_path / "gptme.toml"
    config_path.write_text(TEMPLATE_TOML)

    _merge_project_config(tmp_path, "MyAgent", None)

    # Byte-identical: [prompt] section, comments and unset defaults untouched
    assert config_path.read_text() == TEMPLATE_TOML
    config = get_project_config(tmp_path)
    assert config is not None
    assert config.files == ["README.md"]
    assert config.context_cmd == "scripts/context.sh"


def test_missing_agent_name_is_added_in_place(tmp_path: Path):
    config_path = tmp_path / "gptme.toml"
    config_path.write_text(TEMPLATE_TOML.replace('[agent]\nname = "MyAgent"\n\n', ""))

    _merge_project_config(tmp_path, "MyAgent", None)

    doc = tomlkit.parse(config_path.read_text())
    assert doc["agent"]["name"] == "MyAgent"
    assert "prompt" in doc
    assert '# "WORKFLOW.md"' in config_path.read_text()
    assert "context" not in doc  # no defaults frozen into the file


def test_no_config_creates_default(tmp_path: Path):
    _merge_project_config(tmp_path, "MyAgent", None)

    config = get_project_config(tmp_path)
    assert config is not None
    assert config.agent is not None
    assert config.agent.name == "MyAgent"
