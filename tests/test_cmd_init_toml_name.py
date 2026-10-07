"""gptme init must write a parseable gptme.toml for any project name."""

from pathlib import Path
from unittest.mock import patch

import pytest
import tomlkit
from click.testing import CliRunner

from gptme.cli.cmd_init import _scaffold_from_template, main

NAMES = ['my "proj"', "C:\\new\\dir", "a\\1b", "tab\there", "naïve-项目"]


@pytest.mark.parametrize("name", NAMES)
def test_init_name_roundtrips_through_toml(tmp_path: Path, name: str):
    target = tmp_path / "proj"
    result = CliRunner().invoke(main, [str(target), "--name", name])
    assert result.exit_code == 0, result.output
    data = tomlkit.loads((target / "gptme.toml").read_text())
    assert data["agent"]["name"] == name


@pytest.mark.parametrize("name", NAMES)
def test_template_name_roundtrips_through_toml(tmp_path: Path, name: str):
    target = tmp_path / "proj"

    def fake_clone(cmd, **kwargs):
        target.mkdir()
        (target / "gptme.toml").write_text('[agent]\nname = "template"\n')

        class R:
            returncode = 0
            stderr = ""

        return R()

    with patch("gptme.cli.cmd_init.subprocess.run", fake_clone):
        _scaffold_from_template(target, name, "desc", "org/tpl")
    data = tomlkit.loads((target / "gptme.toml").read_text())
    assert data["agent"]["name"] == name
