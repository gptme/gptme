import pytest

from gptme.gears import parse_gear, resolve_gear


def test_resolve_observe_gear():
    resolution = resolve_gear(0)

    assert resolution.name == "Observe"
    assert resolution.profile_name == "explorer"
    assert resolution.tool_allowlist == ("read", "chats")
    assert resolution.no_confirm is True


def test_resolve_review_gear_keeps_default_tools():
    resolution = resolve_gear(1)

    assert resolution.name == "Review"
    assert resolution.profile_name is None
    assert resolution.tool_allowlist is None
    assert resolution.no_confirm is False


def test_resolve_plan_gear_limits_to_file_tools():
    resolution = resolve_gear(2)

    assert resolution.name == "Plan"
    assert resolution.profile_name is None
    assert resolution.tool_allowlist == ("read", "patch", "save", "append", "ipython")
    assert resolution.no_confirm is False


def test_resolve_execute_gear_sets_developer_no_confirm():
    resolution = resolve_gear(3)

    assert resolution.name == "Execute"
    assert resolution.profile_name == "developer"
    assert resolution.tool_allowlist is None
    assert resolution.no_confirm is True


def test_resolve_integrate_gear_adds_subagent_to_defaults():
    resolution = resolve_gear(4)

    assert resolution.name == "Integrate"
    assert resolution.profile_name == "developer"
    assert resolution.tool_allowlist == ("+subagent",)
    assert resolution.no_confirm is True


@pytest.mark.parametrize("value", [-1, 5, "nope", True])
def test_parse_gear_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="gear|Invalid"):
        parse_gear(value)
