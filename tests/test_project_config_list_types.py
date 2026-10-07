"""Project config list-of-string fields must reject wrong types up front."""

import pytest

from gptme.config import ProjectConfig


@pytest.mark.parametrize(
    ("config_data", "key"),
    [
        ({"prompt": {"files": "README.md"}}, "prompt.files"),
        ({"prompt": {"files": [1, 2]}}, "prompt.files"),
        ({"prompt": {"exclude": "x"}}, "prompt.exclude"),
        ({"files": "README.md"}, "files"),
        ({"lessons": {"dirs": "lessons"}}, "lessons.dirs"),
        ({"lessons": {"dirs": [5]}}, "lessons.dirs"),
        ({"plugins": {"paths": "x"}}, "plugins.paths"),
        ({"plugins": {"enabled": "x"}}, "plugins.enabled"),
    ],
)
def test_wrong_typed_list_field_is_rejected(config_data: dict, key: str):
    with pytest.raises(ValueError, match=f"{key} must be a list of strings"):
        ProjectConfig.from_dict(config_data)


def test_valid_list_fields_still_load():
    cfg = ProjectConfig.from_dict(
        {
            "prompt": {"files": ["README.md"], "exclude": ["x"]},
            "lessons": {"dirs": ["lessons"]},
            "plugins": {"paths": ["p"], "enabled": ["e"]},
        }
    )
    assert cfg.files == ["README.md"]
    assert cfg.exclude == ["x"]
    assert cfg.lessons.dirs == ["lessons"]
    assert cfg.plugins.enabled == ["e"]
